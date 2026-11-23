"""User accounts (patients, care team, caretakers, admin) stored in SQLite.
Passwords are never stored as text: only a salted scrypt hash is kept."""
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from datetime import timedelta
from pathlib import Path

from common import account_id, query_all, query_one, sql_time, text, transaction, table_columns, utc_now

SEED_PATH = Path(__file__).resolve().parent / "seed-users.json"
KEY_LENGTH = 64
ROLES = ["patient", "provider", "nurse", "caretaker", "admin"]
EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
ACCOUNT_COLUMNS = "id, email, name, role, status, created_at"


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        str(password).encode("utf-8"), salt=salt.encode("utf-8"), n=16384, r=8, p=1, dklen=KEY_LENGTH
    )
    return f"{salt}:{digest.hex()}"


def verify_password(password, stored):
    parts = str(stored).split(":")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return False
    salt, expected = parts[0], parts[1]
    actual = hashlib.scrypt(
        str(password).encode("utf-8"), salt=salt.encode("utf-8"), n=16384, r=8, p=1, dklen=KEY_LENGTH
    )
    try:
        expected_bytes = bytes.fromhex(expected)
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected_bytes)


def setup_users(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
          id            INTEGER PRIMARY KEY AUTOINCREMENT,
          email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
          name          TEXT NOT NULL,
          role          TEXT NOT NULL CHECK (role IN ('patient','provider','nurse','caretaker','admin')),
          password_hash TEXT NOT NULL,
          created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    # Migration: accounts created before suspensions existed get the status column.
    if "status" not in table_columns(db, "users"):
        db.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    if query_one(db, "SELECT COUNT(*) AS count FROM users")["count"] > 0:
        return
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    with transaction(db):
        for user in seed:
            db.execute(
                "INSERT INTO users (email, name, role, password_hash) VALUES (?, ?, ?, ?)",
                (user["email"], user["name"], user["role"], hash_password(user["password"])),
            )


def authenticate_user(db, email, password):
    """Returns the public user record if email and password match, otherwise None.
    The record includes status, so the caller can refuse suspended accounts."""
    row = query_one(
        db,
        "SELECT id, email, name, role, status, password_hash FROM users WHERE email = ?",
        (text(email),),
    )
    if not row or not verify_password(str(password or ""), row["password_hash"]):
        return None
    return {k: row[k] for k in ("id", "email", "name", "role", "status")}


# ── Account management (admin) ─────────────────────────────

def _with_account_id(row):
    return {**row, "accountId": account_id(row["id"])}


def list_accounts(db):
    rows = query_all(db, f"SELECT {ACCOUNT_COLUMNS} FROM users ORDER BY created_at, id")
    return [_with_account_id(row) for row in rows]


def invite_account(db, data):
    """Creates an account nobody knows the password for. The person sets their own password
    with "Forgot password?", so no password has to be shared.
    Returns {"account": ...} or {"status", "error"}."""
    clean_name = text(data.get("name"))
    clean_email = text(data.get("email")).lower()
    role = data.get("role")
    if not clean_name or len(clean_name) > 100:
        return {"status": 400, "error": "Please enter the person's name."}
    if not EMAIL_RE.fullmatch(clean_email):
        return {"status": 400, "error": "Please enter a valid email address."}
    if role not in ROLES:
        return {"status": 400, "error": "Please choose a role."}
    try:
        cursor = db.execute(
            "INSERT INTO users (email, name, role, password_hash, status) VALUES (?, ?, ?, ?, 'active')",
            (clean_email, clean_name, role, hash_password(secrets.token_hex(32))),
        )
    except sqlite3.IntegrityError as error:
        if "UNIQUE" in str(error):
            return {"status": 409, "error": "An account with this email already exists."}
        raise
    row = query_one(db, f"SELECT {ACCOUNT_COLUMNS} FROM users WHERE id = ?", (cursor.lastrowid,))
    return {"account": _with_account_id(row)}


def set_account_status(db, account_row_id, status):
    """Sets an account to 'active' or 'suspended'. Returns the account, or None if not found."""
    cursor = db.execute("UPDATE users SET status = ? WHERE id = ?", (status, account_row_id))
    if not cursor.rowcount:
        return None
    if status == "suspended":
        db.execute("DELETE FROM sessions WHERE user_id = ?", (account_row_id,))
    row = query_one(db, f"SELECT {ACCOUNT_COLUMNS} FROM users WHERE id = ?", (account_row_id,))
    return _with_account_id(row)


def register_patient(db, data):
    """Creates a patient account. Returns {"user": ...} or {"status", "error"}."""
    clean_name = text(data.get("name"))
    clean_email = text(data.get("email")).lower()
    clean_password = str(data.get("password") or "")
    if not clean_name or len(clean_name) > 100:
        return {"status": 400, "error": "Please enter your full name."}
    if not EMAIL_RE.fullmatch(clean_email):
        return {"status": 400, "error": "Please enter a valid email address."}
    if len(clean_password) < 8:
        return {"status": 400, "error": "Password must be at least 8 characters."}
    try:
        cursor = db.execute(
            "INSERT INTO users (email, name, role, password_hash) VALUES (?, ?, 'patient', ?)",
            (clean_email, clean_name, hash_password(clean_password)),
        )
    except sqlite3.IntegrityError as error:
        if "UNIQUE" in str(error):
            return {"status": 409, "error": "An account with this email already exists. Try signing in."}
        raise
    return {"user": {"id": cursor.lastrowid, "email": clean_email, "name": clean_name, "role": "patient"}}


# ── Password reset ─────────────────────────────────────────
# No email service is set up yet, so the reset code is written to the server terminal.
RESET_MINUTES = 30


def setup_password_resets(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS password_resets (
          token_hash  TEXT PRIMARY KEY,
          user_id     INTEGER NOT NULL REFERENCES users(id),
          expires_at  TEXT NOT NULL,
          used        INTEGER NOT NULL DEFAULT 0
        )
        """
    )


def hash_reset_token(token):
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def request_password_reset(db, email, log=print):
    """Always returns the same message, so people can't use this to find which emails have accounts."""
    user = query_one(db, "SELECT id, email FROM users WHERE email = ?", (text(email).lower(),))
    if user:
        code = secrets.token_hex(4).upper()  # 8-character code, easy to type
        expires = sql_time(utc_now() + timedelta(minutes=RESET_MINUTES))
        db.execute(
            "INSERT INTO password_resets (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (hash_reset_token(code), user["id"], expires),
        )
        log(f"[password reset] Code for {user['email']}: {code} (valid {RESET_MINUTES} minutes)")
    return {
        "message": "If an account exists for that email, a reset code has been created. Check the server terminal for the code."
    }


def confirm_password_reset(db, data):
    """Returns {"ok": True} or {"status", "error"}."""
    code = text(data.get("code")).upper()
    password = data.get("password")
    if password is None or len(str(password)) < 8:
        return {"status": 400, "error": "Password must be at least 8 characters."}
    row = query_one(
        db,
        "SELECT user_id FROM password_resets WHERE token_hash = ? AND used = 0 AND expires_at > ?",
        (hash_reset_token(code), sql_time(utc_now())),
    )
    if not row:
        return {"status": 400, "error": "That code is wrong or has expired. Request a new one."}
    with transaction(db):
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(str(password)), row["user_id"]))
        db.execute("UPDATE password_resets SET used = 1 WHERE token_hash = ?", (hash_reset_token(code),))
        db.execute("DELETE FROM sessions WHERE user_id = ?", (row["user_id"],))
    return {"ok": True}
