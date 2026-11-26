"""Referrals and referral events for the BHW-RHU monitoring system.

Tables
------
referrals         — one row per referral; mutable status column tracks current state
referral_events   — append-only audit trail of every status transition and note

Design rules enforced here
--------------------------
- referrals.status is constrained to ('pending', 'acknowledged', 'attended', 'closed')
  (task 6.1; order enforcement is task 6.2).
- referral_events is append-only: no UPDATE or DELETE should ever be issued against it.
  The module exposes no function that updates or deletes event rows; any caller that
  attempts a raw UPDATE/DELETE on referral_events violates the design contract.
- A referral cannot be closed without an outcome note (task 6.3).
- created_at / updated_at / created_by follow the same convention as other clinical tables.
- A referral is "overdue" when its status is still 'pending' and the current time is past
  due_by (task 6.4).  When due_by is NULL the fallback deadline is DEFAULT_OVERDUE_HOURS
  after created_at.  Task 0.3 (protocol time limits) is still open, so this value is a
  named constant that can be replaced once the partner confirms the protocol.
"""

from common import query_all, query_one, sql_time, text, transaction, utc_now

REFERRAL_STATUSES = ("pending", "acknowledged", "attended", "closed")

# Roles that may create or manage referrals
REFERRAL_WRITE_ROLES  = ("bhw", "midwife", "physician", "rhu_admin")
REFERRAL_REVIEW_ROLES = ("physician", "rhu_admin", "midwife")

# Default fallback: a pending referral with no due_by is considered overdue after this
# many hours.  Task 0.3 (confirming the protocol time limit with the partner) is still
# open; replace this value once the partner's protocol is recorded in tasks.md.
DEFAULT_OVERDUE_HOURS = 48


# ── Schema ────────────────────────────────────────────────────────────────────

def setup_referrals(db):
    """Create referrals and referral_events tables if they do not exist.

    Called once at database startup by db.open_database().
    """
    db.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
          id                   INTEGER PRIMARY KEY AUTOINCREMENT,
          mother_id            INTEGER NOT NULL REFERENCES mothers(id),
          from_user_id         INTEGER NOT NULL REFERENCES users(id),
          to_facility_or_user  TEXT    NOT NULL DEFAULT '',
          reason               TEXT    NOT NULL DEFAULT '',
          status               TEXT    NOT NULL DEFAULT 'pending'
                                 CHECK (status IN ('pending', 'acknowledged', 'attended', 'closed')),
          due_by               TEXT,
          created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
          created_by           INTEGER NOT NULL REFERENCES users(id),
          updated_at           TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # referral_events is append-only. This module never issues UPDATE or DELETE
    # against this table. The trigger below provides a database-level guard so
    # that accidental updates or deletes are blocked even if called outside this
    # module.
    db.execute("""
        CREATE TABLE IF NOT EXISTS referral_events (
          id           INTEGER PRIMARY KEY AUTOINCREMENT,
          referral_id  INTEGER NOT NULL REFERENCES referrals(id),
          status       TEXT    NOT NULL
                         CHECK (status IN ('pending', 'acknowledged', 'attended', 'closed')),
          actor        INTEGER NOT NULL REFERENCES users(id),
          at           TEXT    NOT NULL DEFAULT (datetime('now')),
          note         TEXT    NOT NULL DEFAULT ''
        )
    """)

    # Append-only enforcement: triggers that raise an error if any code attempts
    # to UPDATE or DELETE a referral_events row.
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS referral_events_no_update
        BEFORE UPDATE ON referral_events
        BEGIN
          SELECT RAISE(ABORT, 'referral_events is append-only: UPDATE is not permitted');
        END
    """)

    db.execute("""
        CREATE TRIGGER IF NOT EXISTS referral_events_no_delete
        BEFORE DELETE ON referral_events
        BEGIN
          SELECT RAISE(ABORT, 'referral_events is append-only: DELETE is not permitted');
        END
    """)


# ── Serialisation ─────────────────────────────────────────────────────────────

def _to_referral(row):
    return {
        "id":                 row["id"],
        "mother_id":          row["mother_id"],
        "from_user_id":       row["from_user_id"],
        "to_facility_or_user": row["to_facility_or_user"],
        "reason":             row["reason"],
        "status":             row["status"],
        "due_by":             row["due_by"],
        "created_at":         row["created_at"],
        "created_by":         row["created_by"],
        "updated_at":         row["updated_at"],
    }


def _to_event(row):
    return {
        "id":          row["id"],
        "referral_id": row["referral_id"],
        "status":      row["status"],
        "actor":       row["actor"],
        "at":          row["at"],
        "note":        row["note"],
    }


# ── Business logic ────────────────────────────────────────────────────────────

def create_referral(db, user, data):
    """Create a new referral in 'pending' status and record the initial event.

    Returns {"referral": {...}} on success.
    Returns {"status": int, "error": str} on validation failure.
    """
    mother_id           = data.get("mother_id") or data.get("motherId")
    to_facility_or_user = text(data.get("to_facility_or_user") or data.get("toFacilityOrUser") or "")
    reason              = text(data.get("reason") or "")
    due_by              = text(data.get("due_by") or data.get("dueBy") or "") or None

    if not mother_id:
        return {"status": 400, "error": "mother_id is required."}
    if not reason:
        return {"status": 400, "error": "reason is required."}

    try:
        mother_id = int(mother_id)
    except (TypeError, ValueError):
        return {"status": 400, "error": "mother_id must be an integer."}

    with transaction(db):
        cursor = db.execute(
            """INSERT INTO referrals
               (mother_id, from_user_id, to_facility_or_user, reason, status, due_by, created_by)
               VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
            (mother_id, user["id"], to_facility_or_user, reason, due_by, user["id"]),
        )
        referral_id = cursor.lastrowid
        # Record the initial event (append-only; no updates ever follow)
        db.execute(
            """INSERT INTO referral_events (referral_id, status, actor, note)
               VALUES (?, 'pending', ?, 'Referral created')""",
            (referral_id, user["id"]),
        )

    row = query_one(db, "SELECT * FROM referrals WHERE id = ?", (referral_id,))
    return {"referral": _to_referral(row)}


def get_referral(db, referral_id):
    """Return a referral dict or None."""
    row = query_one(db, "SELECT * FROM referrals WHERE id = ?", (referral_id,))
    return _to_referral(row) if row else None


def list_referrals(db, mother_id=None, status=None):
    """Return referrals, optionally filtered by mother or status."""
    where = []
    values = []
    if mother_id is not None:
        where.append("mother_id = ?")
        values.append(mother_id)
    if status:
        where.append("status = ?")
        values.append(status)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = query_all(db, f"SELECT * FROM referrals {clause} ORDER BY created_at", values)
    return [_to_referral(r) for r in rows]


def list_events(db, referral_id):
    """Return all events for a referral in chronological order (append-only read)."""
    rows = query_all(
        db,
        "SELECT * FROM referral_events WHERE referral_id = ? ORDER BY at, id",
        (referral_id,),
    )
    return [_to_event(r) for r in rows]


def list_overdue_referrals(db, now=None, overdue_hours=DEFAULT_OVERDUE_HOURS):
    """Return every pending referral that has passed its acknowledgement deadline.

    A referral is overdue when its status is still 'pending' AND either:
      - due_by is set and the current time is past due_by, OR
      - due_by is NULL and more than overdue_hours have elapsed since created_at.

    Parameters
    ----------
    db            : database connection
    now           : datetime (UTC) to use as "current time"; defaults to utc_now().
                    Pass a fixed datetime in tests to get deterministic results.
    overdue_hours : int — fallback deadline in hours when due_by is NULL.
                    Defaults to DEFAULT_OVERDUE_HOURS (48 h) until task 0.3 is resolved.

    Returns a list of referral dicts, oldest first.
    """
    if now is None:
        now = utc_now()
    now_str = sql_time(now)
    rows = query_all(
        db,
        """SELECT * FROM referrals
           WHERE status = 'pending'
             AND (
                   (due_by IS NOT NULL AND due_by < ?)
                   OR
                   (due_by IS NULL
                    AND datetime(created_at, ? || ' hours') < ?)
                 )
           ORDER BY created_at""",
        (now_str, str(overdue_hours), now_str),
    )
    return [_to_referral(r) for r in rows]


# ── Task 6.5: Follow-up list ──────────────────────────────────────────────────

def list_mothers_with_open_referrals(db, user=None):
    """Return mothers that have at least one referral with status != 'closed'.

    Each entry includes the mother record and a summary of open referrals so
    the BHW / RHU dashboard can show which mothers need follow-up.

    The *user* argument is accepted for future role-scoping but is not yet
    used for filtering (all roles that reach this function see the same list;
    access control is enforced at the route level in app.py).

    Returns a list of dicts:
        {
          "mother_id":      int,
          "mother_name":    str,
          "open_referrals": [
              {"id": int, "status": str, "reason": str, "due_by": str|None,
               "created_at": str}
          ]
        }
    """
    # Fetch distinct mothers that have at least one non-closed referral,
    # then gather the open referral summaries for each.
    mother_rows = query_all(
        db,
        """SELECT DISTINCT m.id, m.name
           FROM mothers m
           JOIN referrals r ON r.mother_id = m.id
           WHERE r.status != 'closed'
           ORDER BY m.name""",
    )

    result = []
    for m in mother_rows:
        open_refs = query_all(
            db,
            """SELECT id, status, reason, due_by, created_at
               FROM referrals
               WHERE mother_id = ? AND status != 'closed'
               ORDER BY created_at""",
            (m["id"],),
        )
        result.append({
            "mother_id":      m["id"],
            "mother_name":    m["name"],
            "open_referrals": [
                {
                    "id":         r["id"],
                    "status":     r["status"],
                    "reason":     r["reason"],
                    "due_by":     r["due_by"],
                    "created_at": r["created_at"],
                }
                for r in open_refs
            ],
        })
    return result


# ── Status-change logic ───────────────────────────────────────────────────────

# Valid one-step transitions (task 6.2). Skipping any step is refused.
_NEXT_STATUS = {
    "pending":      "acknowledged",
    "acknowledged": "attended",
    "attended":     "closed",
}


def advance_referral_status(db, user, referral_id, data):
    """Advance a referral to the next status in the lifecycle.

    Valid progression: pending → acknowledged → attended → closed.
    Skipping a step returns {"status": 400, "error": ...}.
    Closing without an outcome note returns {"status": 400, "error": ...} (task 6.3).

    Returns {"referral": {...}, "event": {...}} on success.
    """
    new_status = text(data.get("status") or "")
    note       = text(data.get("note") or "")

    if new_status not in REFERRAL_STATUSES:
        return {"status": 400, "error": f"status must be one of: {', '.join(REFERRAL_STATUSES)}."}

    referral = get_referral(db, referral_id)
    if not referral:
        return {"status": 404, "error": "Referral not found."}

    current = referral["status"]

    if current == "closed":
        return {"status": 400, "error": "Referral is already closed."}

    expected_next = _NEXT_STATUS.get(current)
    if new_status != expected_next:
        return {
            "status": 400,
            "error": (
                f"Invalid status transition: '{current}' → '{new_status}'. "
                f"The only permitted next status is '{expected_next}'."
            ),
        }

    # Task 6.3: closing requires an outcome note.
    if new_status == "closed" and not note:
        return {"status": 400, "error": "An outcome note is required to close a referral."}

    with transaction(db):
        db.execute(
            "UPDATE referrals SET status = ?, updated_at = datetime('now') WHERE id = ?",
            (new_status, referral_id),
        )
        # Append-only event row; never updated or deleted.
        cursor = db.execute(
            "INSERT INTO referral_events (referral_id, status, actor, note) VALUES (?, ?, ?, ?)",
            (referral_id, new_status, user["id"], note),
        )
        event_id = cursor.lastrowid

    updated = get_referral(db, referral_id)
    event_row = query_one(db, "SELECT * FROM referral_events WHERE id = ?", (event_id,))
    return {"referral": updated, "event": _to_event(event_row)}



