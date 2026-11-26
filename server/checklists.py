"""Checklists for the BHW-RHU monitoring system (Phase 4).

Tables
------
checklists  — approved assessment forms used during home and clinical visits

Design rules enforced here
--------------------------
- Only checklists with `approved_by` set can be used in the field (task 4.1).
  Fetching a checklist whose `approved_by` is NULL or empty returns HTTP 403.
- `stage` is restricted to 'prenatal' or 'postpartum'.
- `items` is stored as JSON text; callers supply and consume it as a plain list/dict.
- No thresholds or scores are calculated here (task 4.3).
"""

import json

from common import query_all, query_one, text, transaction


# Roles that may create checklists (admin-level only)
CHECKLIST_CREATE_ROLES = ("rhu_admin", "sysadmin")

# Roles that may retrieve a checklist for field use
CHECKLIST_READ_ROLES = ("bhw", "midwife", "physician", "rhu_admin", "sysadmin")

VALID_STAGES = ("prenatal", "postpartum")


# ── Schema ────────────────────────────────────────────────────────────────────

def setup_checklists(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS checklists (
          id          INTEGER PRIMARY KEY AUTOINCREMENT,
          title       TEXT    NOT NULL,
          stage       TEXT    NOT NULL
                        CHECK (stage IN ('prenatal', 'postpartum')),
          version     TEXT    NOT NULL DEFAULT '1',
          approved_by TEXT,
          approved_at TEXT,
          items       TEXT    NOT NULL DEFAULT '[]',
          created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
          updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)


# ── Serialisation ─────────────────────────────────────────────────────────────

def _to_checklist(row):
    items = row["items"]
    try:
        items = json.loads(items) if isinstance(items, str) else items
    except (ValueError, TypeError):
        items = []
    return {
        "id":          row["id"],
        "title":       row["title"],
        "stage":       row["stage"],
        "version":     row["version"],
        "approvedBy":  row["approved_by"],
        "approvedAt":  row["approved_at"],
        "items":       items,
        "createdAt":   row["created_at"],
        "updatedAt":   row["updated_at"],
    }


# ── Business logic ────────────────────────────────────────────────────────────

def create_checklist(db, user, data):
    """Create a new checklist record.

    Returns {"checklist": {...}} on success.
    Returns {"status": int, "error": str} on failure.
    """
    title       = text(data.get("title"))
    stage       = text(data.get("stage"))
    version     = text(data.get("version") or "1")
    approved_by = text(data.get("approvedBy") or data.get("approved_by") or "")
    approved_at = text(data.get("approvedAt") or data.get("approved_at") or "")
    items       = data.get("items", [])

    # Validation
    if not title:
        return {"status": 400, "error": "title is required."}
    if stage not in VALID_STAGES:
        return {"status": 400, "error": f"stage must be one of: {', '.join(VALID_STAGES)}."}

    # Serialise items to JSON
    try:
        items_json = json.dumps(items)
    except (TypeError, ValueError):
        return {"status": 400, "error": "items must be a JSON-serialisable list."}

    with transaction(db):
        cursor = db.execute(
            """INSERT INTO checklists
               (title, stage, version, approved_by, approved_at, items)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                title,
                stage,
                version or "1",
                approved_by or None,
                approved_at or None,
                items_json,
            ),
        )
        checklist_id = cursor.lastrowid

    row = query_one(db, "SELECT * FROM checklists WHERE id = ?", (checklist_id,))
    return {"checklist": _to_checklist(row)}


def get_checklist(db, checklist_id):
    """Retrieve a checklist by id.

    Returns {"checklist": {...}} when found and approved.
    Returns {"status": 404, "error": ...} when not found.
    Returns {"status": 403, "error": ...} when found but not approved.
    """
    row = query_one(db, "SELECT * FROM checklists WHERE id = ?", (checklist_id,))
    if not row:
        return {"status": 404, "error": "Checklist not found."}
    if not row["approved_by"]:
        return {"status": 403, "error": "This checklist has not been approved and cannot be used."}
    return {"checklist": _to_checklist(row)}


def list_checklists(db):
    """Return all checklists (admin view — includes unapproved)."""
    rows = query_all(db, "SELECT * FROM checklists ORDER BY title")
    return [_to_checklist(r) for r in rows]
