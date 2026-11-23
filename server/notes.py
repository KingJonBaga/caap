"""Caseload notes: short follow-up notes the care team writes for their list of patients."""
from common import query_all, query_one


def setup_notes(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS caseload_notes (
          id          INTEGER PRIMARY KEY AUTOINCREMENT,
          author      TEXT NOT NULL,
          body        TEXT NOT NULL,
          created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


def list_notes(db, limit=50):
    return query_all(
        db, "SELECT id, author, body, created_at FROM caseload_notes ORDER BY id DESC LIMIT ?", (limit,)
    )


def add_note(db, user, body):
    """Returns {"note": ...} or {"status", "error"}."""
    text = str(body or "").strip()
    if not text:
        return {"status": 400, "error": "Enter a follow-up note first."}
    if len(text) > 1000:
        return {"status": 400, "error": "Notes can be up to 1,000 characters."}
    cursor = db.execute("INSERT INTO caseload_notes (author, body) VALUES (?, ?)", (user["name"], text))
    note = query_one(db, "SELECT id, author, body, created_at FROM caseload_notes WHERE id = ?", (cursor.lastrowid,))
    return {"note": note}
