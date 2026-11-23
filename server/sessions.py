"""Login sessions. The browser gets a random token; the database keeps only its SHA-256 hash."""
import hashlib
import secrets
from datetime import timedelta

from common import query_one, sql_time, utc_now

SESSION_HOURS = 8


def hash_token(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def setup_sessions(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash  TEXT PRIMARY KEY,
          user_id     INTEGER NOT NULL REFERENCES users(id),
          created_at  TEXT NOT NULL DEFAULT (datetime('now')),
          expires_at  TEXT NOT NULL
        )
        """
    )


def create_session(db, user):
    token = secrets.token_hex(32)
    expires = sql_time(utc_now() + timedelta(hours=SESSION_HOURS))
    db.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
        (hash_token(token), user["id"], expires),
    )
    return token


def get_session_user(db, token):
    """Returns the user for a valid, unexpired token, otherwise None."""
    if not token:
        return None
    db.execute("DELETE FROM sessions WHERE expires_at < datetime('now')")
    return query_one(
        db,
        """
        SELECT u.id, u.email, u.name, u.role
        FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = ?
        """,
        (hash_token(token),),
    )


def delete_session(db, token):
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))
