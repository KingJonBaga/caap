"""Role migration: rebuilds the users table with the new BHW-RHU role set.

Old role → new role mapping (design.md section 3):
  patient   → mother
  provider  → physician
  nurse     → midwife
  caretaker → caretaker  (kept for now; retire manually later)
  admin     → sysadmin   (default; can be changed to rhu_admin manually after migration)

SQLite cannot ALTER a CHECK constraint in place, so this migration:
  1. Creates users_new with the new CHECK constraint.
  2. Copies all rows, remapping roles.
  3. Drops users (which also drops its indexes).
  4. Renames users_new → users.
  5. Re-creates the UNIQUE index on email.

The whole thing runs in a single transaction; any error triggers a rollback
so the database is left untouched.

Usage:
  python server/migrate_roles.py              # migrates data/caap.db in place
  python server/migrate_roles.py /path/to.db  # migrates the given file
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

from common import open_connection, query_all, transaction  # noqa: E402

# Mapping from old role to new role.
ROLE_MAP = {
    "patient":   "mother",
    "provider":  "physician",
    "nurse":     "midwife",
    "caretaker": "caretaker",
    "admin":     "sysadmin",
}

NEW_ROLES = (
    "mother", "bhw", "midwife", "physician",
    "rhu_admin", "sysadmin", "caretaker",
    # Legacy roles retained so old tokens still resolve during a rolling deploy.
    "patient", "provider", "nurse", "admin",
)


def migrate(db):
    """Run the role migration on *db* (an open sqlite3 connection).

    Returns a list of (email, old_role, new_role) for every row that changed.
    Raises on any error; the caller is responsible for the transaction.
    """
    placeholders = ",".join(f"'{r}'" for r in NEW_ROLES)

    # 1. Create the replacement table.
    db.execute(f"""
        CREATE TABLE users_new (
          id            INTEGER PRIMARY KEY AUTOINCREMENT,
          email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
          name          TEXT NOT NULL,
          role          TEXT NOT NULL CHECK (role IN ({placeholders})),
          password_hash TEXT NOT NULL,
          status        TEXT NOT NULL DEFAULT 'active',
          created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # 2. Copy rows, remapping roles.
    rows = query_all(db, "SELECT id, email, name, role, password_hash, status, created_at FROM users")
    changes = []
    for row in rows:
        old_role = row["role"]
        new_role = ROLE_MAP.get(old_role, old_role)   # unknown roles stay as-is
        db.execute(
            """INSERT INTO users_new
               (id, email, name, role, password_hash, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (row["id"], row["email"], row["name"], new_role,
             row["password_hash"], row.get("status", "active"), row["created_at"]),
        )
        if new_role != old_role:
            changes.append((row["email"], old_role, new_role))

    # 3. Drop the old table (also drops its indexes and triggers).
    db.execute("DROP TABLE users")

    # 4. Rename.
    db.execute("ALTER TABLE users_new RENAME TO users")

    return changes


def run(db_path):
    db = open_connection(str(db_path))
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA foreign_keys = OFF")   # avoid FK checks during the rename
    try:
        with transaction(db):
            changes = migrate(db)
    finally:
        db.execute("PRAGMA foreign_keys = ON")

    if changes:
        print(f"Migration complete. {len(changes)} role(s) updated:")
        for email, old, new in changes:
            print(f"  {email}: {old} → {new}")
    else:
        print("Migration complete. No roles needed updating.")
    db.close()


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "caap.db"
    if not path.exists():
        print(f"Database not found: {path}")
        sys.exit(1)
    run(path)
