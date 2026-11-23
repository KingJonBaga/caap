"""Audit log: records who did what, and when, for patient and account actions."""
from common import query_all


def setup_audit(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
          id          INTEGER PRIMARY KEY AUTOINCREMENT,
          at          TEXT NOT NULL DEFAULT (datetime('now')),
          user_email  TEXT NOT NULL,
          role        TEXT NOT NULL,
          action      TEXT NOT NULL,
          detail      TEXT NOT NULL DEFAULT ''
        )
        """
    )


def record_audit(db, user, action, detail=""):
    db.execute(
        "INSERT INTO audit_log (user_email, role, action, detail) VALUES (?, ?, ?, ?)",
        (user["email"] if user else "anonymous", user["role"] if user else "none", action, str(detail)),
    )


def list_audit(db, limit=200):
    return query_all(
        db,
        "SELECT id, at, user_email, role, action, detail FROM audit_log ORDER BY id DESC LIMIT ?",
        (limit,),
    )
