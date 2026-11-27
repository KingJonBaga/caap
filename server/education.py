"""Approved education materials for mothers (Phase 10).

Table
-----
education_items  — pregnancy and postpartum information for mothers

Rules enforced here
-------------------
- Only items with `approved_by` set are shown to mothers (task 10.1).
- A mother sees only approved items for her stage (task 10.2). Items for the
  wrong stage are never returned to her.
- Staff can see unapproved items so they can review them, and the response says
  whether each item is approved.
- Creating an item requires a qualified professional (physician or RHU admin).
"""

from common import query_all, query_one, text, transaction, utc_now


EDUCATION_WRITE_ROLES = ("physician", "rhu_admin")
EDUCATION_STAFF_ROLES = ("bhw", "midwife", "physician", "rhu_admin", "sysadmin")
MOTHER_ROLE = "mother"
VALID_STAGES = ("prenatal", "postpartum")


def setup_education(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS education_items (
          id          INTEGER PRIMARY KEY AUTOINCREMENT,
          title       TEXT NOT NULL,
          stage       TEXT NOT NULL CHECK (stage IN ('prenatal', 'postpartum')),
          body        TEXT NOT NULL,
          approved_by TEXT,
          approved_at TEXT,
          created_by  TEXT NOT NULL DEFAULT '',
          created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)


def _to_item(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "stage": row["stage"],
        "body": row["body"],
        "approvedBy": row["approved_by"] or None,
        "approvedAt": row["approved_at"] or None,
        "approved": bool(row["approved_by"]),
    }


def create_item(db, user, data):
    """Returns {"item": ...} or {"status", "error"}."""
    title = text(data.get("title"))
    body = text(data.get("body"))
    stage = data.get("stage")
    approved_by = text(data.get("approvedBy"))
    if not title or len(title) > 200:
        return {"status": 400, "error": "Please enter a title (up to 200 characters)."}
    if not body or len(body) > 5000:
        return {"status": 400, "error": "Please enter the material text (up to 5,000 characters)."}
    if stage not in VALID_STAGES:
        return {"status": 400, "error": "Stage must be prenatal or postpartum."}
    if not approved_by:
        return {"status": 400, "error": "Name the professional who approved this material."}
    now = utc_now().strftime("%Y-%m-%d %H:%M:%S")
    cursor = db.execute(
        """INSERT INTO education_items (title, stage, body, approved_by, approved_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (title, stage, body, approved_by, now, user["email"]),
    )
    return {"item": _to_item(query_one(db, "SELECT * FROM education_items WHERE id = ?", (cursor.lastrowid,)))}


def list_items(db, user_role, stage=None):
    """Mothers get approved items only. Staff get every item, with its approval status."""
    where = []
    values = []
    if user_role == MOTHER_ROLE:
        where.append("approved_by IS NOT NULL AND approved_by != ''")
    if stage in VALID_STAGES:
        where.append("stage = ?")
        values.append(stage)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = query_all(db, f"SELECT * FROM education_items {clause} ORDER BY stage, id", values)
    return [_to_item(row) for row in rows]
