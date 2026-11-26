"""Mothers and pregnancy history for the BHW-RHU monitoring system.

Tables
------
mothers           — one row per registered mother
pregnancy_history — one row per pregnancy, linked to a mother

Design rules enforced here
--------------------------
- consent_recorded_at must be present before a mother can be saved (task 2.4)
- duplicate_key (hash of normalised name + date_of_birth) prevents double registration (task 2.3)
- Every read of a mother record is written to audit_log by the route handler (task 2.5)
- No risk scores or thresholds are calculated here
"""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from common import query_all, query_one, text, transaction, utc_now, sql_time, table_columns

# Roles that may read mothers scoped to their own BHW assignment
BHW_ROLES     = ("bhw",)
# Roles that may read all mothers at their facility
CLINICAL_ROLES = ("midwife", "physician", "nurse", "provider")
# Roles that may read all mothers in the whole RHU
RHU_ROLES      = ("rhu_admin", "sysadmin", "admin")
# Roles that may register (create) a new mother record.
# physician/nurse/provider can read and document, but not register.
MOTHERS_WRITE_ROLES = ("bhw", "midwife", "rhu_admin", "sysadmin", "admin")

MOTHER_STATUS = ("prenatal", "postpartum", "closed")


# ── Schema ────────────────────────────────────────────────────────────────────

def setup_mothers(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS mothers (
          id                  INTEGER PRIMARY KEY AUTOINCREMENT,
          name                TEXT    NOT NULL,
          date_of_birth       TEXT,
          address_area        TEXT    NOT NULL DEFAULT '',
          barangay_id         TEXT    NOT NULL DEFAULT '',
          contact             TEXT,
          edd                 TEXT,
          assigned_bhw_id     INTEGER REFERENCES users(id),
          facility_id         TEXT    NOT NULL DEFAULT 'BHC-001',
          status              TEXT    NOT NULL DEFAULT 'prenatal'
                                CHECK (status IN ('prenatal','postpartum','closed')),
          duplicate_key       TEXT    NOT NULL UNIQUE,
          consent_recorded_at TEXT    NOT NULL,
          created_by          INTEGER NOT NULL REFERENCES users(id),
          created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
          updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS pregnancy_history (
          id                      INTEGER PRIMARY KEY AUTOINCREMENT,
          mother_id               INTEGER NOT NULL REFERENCES mothers(id),
          gravida                 INTEGER NOT NULL DEFAULT 1,
          para                    INTEGER NOT NULL DEFAULT 0,
          previous_complications  TEXT    NOT NULL DEFAULT '',
          recorded_by             INTEGER NOT NULL REFERENCES users(id),
          created_at              TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)


# ── Duplicate key ─────────────────────────────────────────────────────────────

def _make_duplicate_key(name, date_of_birth):
    """Hash of normalised name + DOB.  Used to detect duplicate registrations.
    date_of_birth may be None/empty — still produces a stable key."""
    normalised = " ".join(text(name).lower().split())
    dob = text(date_of_birth).strip() if date_of_birth else ""
    raw = f"{normalised}|{dob}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── Serialisation ─────────────────────────────────────────────────────────────

def _to_mother(row):
    return {
        "id":                row["id"],
        "name":              row["name"],
        "dateOfBirth":       row["date_of_birth"],
        "addressArea":       row["address_area"],
        "barangayId":        row["barangay_id"],
        "edd":               row["edd"],
        "assignedBhwId":     row["assigned_bhw_id"],
        "facilityId":        row["facility_id"],
        "status":            row["status"],
        "consentRecordedAt": row["consent_recorded_at"],
        "createdBy":         row["created_by"],
        "createdAt":         row["created_at"],
        "updatedAt":         row["updated_at"],
    }


# ── Queries ───────────────────────────────────────────────────────────────────

def list_mothers(db, user):
    """Return mothers visible to *user* based on their role.

    BHW       → only mothers assigned to them
    midwife/physician/nurse/provider → all at their facility (all for now,
                facility scoping added when facilities table exists)
    rhu_admin/sysadmin/admin → all
    """
    role = user.get("role", "")
    uid  = user.get("id")

    if role in BHW_ROLES:
        rows = query_all(
            db,
            "SELECT * FROM mothers WHERE assigned_bhw_id = ? ORDER BY name",
            (uid,),
        )
    elif role in CLINICAL_ROLES:
        rows = query_all(db, "SELECT * FROM mothers ORDER BY name")
    elif role in RHU_ROLES:
        rows = query_all(db, "SELECT * FROM mothers ORDER BY name")
    else:
        return []          # unrecognised role sees nothing
    return [_to_mother(r) for r in rows]


def get_mother(db, mother_id):
    row = query_one(db, "SELECT * FROM mothers WHERE id = ?", (mother_id,))
    return _to_mother(row) if row else None


def create_mother(db, user, data):
    """Register a new mother.

    Returns {"mother": {...}} on success.
    Returns {"status": int, "error": str} on failure.
    """
    name          = text(data.get("name"))
    date_of_birth = text(data.get("dateOfBirth") or data.get("date_of_birth") or "")
    address_area  = text(data.get("addressArea") or data.get("address_area") or "")
    barangay_id   = text(data.get("barangayId")  or data.get("barangay_id")  or "")
    contact       = data.get("contact")
    edd           = text(data.get("edd") or "")
    facility_id   = text(data.get("facilityId") or data.get("facility_id") or "BHC-001")
    assigned_bhw_id = data.get("assignedBhwId") or data.get("assigned_bhw_id")
    consent_at    = text(data.get("consentRecordedAt") or data.get("consent_recorded_at") or "")
    created_by    = user["id"]

    # ── Validation ────────────────────────────────────────
    if not name or len(name) > 150:
        return {"status": 400, "error": "Please enter the mother's full name (max 150 characters)."}

    if not consent_at:
        return {"status": 400, "error": "Consent must be recorded before saving a mother record."}

    dup_key = _make_duplicate_key(name, date_of_birth)

    # ── Insert ────────────────────────────────────────────
    try:
        with transaction(db):
            cursor = db.execute(
                """INSERT INTO mothers
                   (name, date_of_birth, address_area, barangay_id, contact,
                    edd, assigned_bhw_id, facility_id, status,
                    duplicate_key, consent_recorded_at, created_by)
                   VALUES (?,?,?,?,?,?,?,?,'prenatal',?,?,?)""",
                (name,
                 date_of_birth or None,
                 address_area,
                 barangay_id,
                 contact or None,
                 edd or None,
                 assigned_bhw_id or created_by,
                 facility_id,
                 dup_key,
                 consent_at,
                 created_by),
            )
            mother_id = cursor.lastrowid
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" in str(exc) and "duplicate_key" in str(exc):
            return {"status": 409, "error": "A mother with this name and date of birth is already registered."}
        raise

    row = query_one(db, "SELECT * FROM mothers WHERE id = ?", (mother_id,))
    return {"mother": _to_mother(row)}


# ── Pregnancy history ─────────────────────────────────────────────────────────

def add_pregnancy_history(db, user, mother_id, data):
    """Add or update the pregnancy history for a mother.

    Returns {"history": {...}} on success, {"status", "error"} on failure.
    """
    mother = get_mother(db, mother_id)
    if not mother:
        return {"status": 404, "error": "Mother not found."}

    gravida = data.get("gravida")
    para    = data.get("para")
    complications = text(data.get("previousComplications") or data.get("previous_complications") or "")

    if not isinstance(gravida, int) or gravida < 1:
        return {"status": 400, "error": "gravida must be a positive integer."}
    if not isinstance(para, int) or para < 0:
        return {"status": 400, "error": "para must be a non-negative integer."}

    with transaction(db):
        cursor = db.execute(
            """INSERT INTO pregnancy_history
               (mother_id, gravida, para, previous_complications, recorded_by)
               VALUES (?,?,?,?,?)""",
            (mother_id, gravida, para, complications, user["id"]),
        )
        row_id = cursor.lastrowid

    row = query_one(db, "SELECT * FROM pregnancy_history WHERE id = ?", (row_id,))
    return {"history": dict(row)}


def list_pregnancy_history(db, mother_id):
    return query_all(
        db,
        "SELECT * FROM pregnancy_history WHERE mother_id = ? ORDER BY created_at",
        (mother_id,),
    )
