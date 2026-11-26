"""Tests for the role migration (migrate_roles.py).

Run with: py -m unittest discover -s tests
All tests use in-memory databases and never touch data/caap.db.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

from common import open_connection, query_all, query_one, transaction  # noqa: E402
from migrate_roles import migrate, ROLE_MAP  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_old_db():
    """Return an in-memory database with the original users table schema (old roles only)."""
    db = open_connection(":memory:")
    db.execute("PRAGMA foreign_keys = OFF")
    db.execute("""
        CREATE TABLE users (
          id            INTEGER PRIMARY KEY AUTOINCREMENT,
          email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
          name          TEXT NOT NULL,
          role          TEXT NOT NULL CHECK (role IN ('patient','provider','nurse','caretaker','admin')),
          password_hash TEXT NOT NULL,
          status        TEXT NOT NULL DEFAULT 'active',
          created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    return db


def _seed(db, rows):
    """Insert a list of (email, name, role) tuples with a dummy password hash."""
    for email, name, role in rows:
        db.execute(
            "INSERT INTO users (email, name, role, password_hash) VALUES (?, ?, ?, 'x:y')",
            (email, name, role),
        )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestRoleMigration(unittest.TestCase):

    def _run(self, db):
        """Run the migration inside a transaction the same way the script does."""
        db.execute("PRAGMA foreign_keys = OFF")
        try:
            with transaction(db):
                changes = migrate(db)
        finally:
            db.execute("PRAGMA foreign_keys = ON")
        return changes

    # -- Happy path -----------------------------------------------------------

    def test_every_old_role_is_remapped_correctly(self):
        db = _make_old_db()
        _seed(db, [
            ("patient@x.com",   "P1", "patient"),
            ("provider@x.com",  "P2", "provider"),
            ("nurse@x.com",     "P3", "nurse"),
            ("caretaker@x.com", "P4", "caretaker"),
            ("admin@x.com",     "P5", "admin"),
        ])
        self._run(db)
        rows = {r["email"]: r["role"] for r in query_all(db, "SELECT email, role FROM users")}
        self.assertEqual(rows["patient@x.com"],   "mother")
        self.assertEqual(rows["provider@x.com"],  "physician")
        self.assertEqual(rows["nurse@x.com"],     "midwife")
        self.assertEqual(rows["caretaker@x.com"], "caretaker")
        self.assertEqual(rows["admin@x.com"],     "sysadmin")

    def test_migration_preserves_all_rows(self):
        db = _make_old_db()
        _seed(db, [(f"u{i}@x.com", f"User {i}", "patient") for i in range(10)])
        self._run(db)
        count = query_one(db, "SELECT COUNT(*) AS n FROM users")["n"]
        self.assertEqual(count, 10)

    def test_migration_preserves_ids_emails_names_passwords_and_status(self):
        db = _make_old_db()
        db.execute(
            "INSERT INTO users (id, email, name, role, password_hash, status) VALUES (42, 'x@x.com', 'X', 'admin', 'salt:hash', 'suspended')"
        )
        self._run(db)
        row = query_one(db, "SELECT * FROM users WHERE id = 42")
        self.assertEqual(row["id"],            42)
        self.assertEqual(row["email"],         "x@x.com")
        self.assertEqual(row["name"],          "X")
        self.assertEqual(row["password_hash"], "salt:hash")
        self.assertEqual(row["status"],        "suspended")
        self.assertEqual(row["role"],          "sysadmin")

    def test_migration_returns_only_the_changed_rows(self):
        db = _make_old_db()
        _seed(db, [
            ("a@x.com", "A", "patient"),    # changes
            ("b@x.com", "B", "caretaker"),  # stays caretaker — no change
        ])
        changes = self._run(db)
        emails = [c[0] for c in changes]
        self.assertIn("a@x.com", emails)
        self.assertNotIn("b@x.com", emails)

    def test_new_roles_are_accepted_after_migration(self):
        """After migration the table should accept bhw, rhu_admin, etc."""
        db = _make_old_db()
        _seed(db, [("any@x.com", "A", "patient")])
        self._run(db)
        # Should not raise
        db.execute(
            "INSERT INTO users (email, name, role, password_hash) VALUES ('bhw@x.com', 'B', 'bhw', 'x:y')"
        )
        db.execute(
            "INSERT INTO users (email, name, role, password_hash) VALUES ('rhu@x.com', 'R', 'rhu_admin', 'x:y')"
        )
        count = query_one(db, "SELECT COUNT(*) AS n FROM users")["n"]
        self.assertEqual(count, 3)

    def test_migration_is_idempotent_on_already_migrated_database(self):
        """Running migrate() twice should not error and should report no further changes."""
        db = _make_old_db()
        _seed(db, [("p@x.com", "P", "patient")])
        self._run(db)
        # Second run: roles are already 'mother' etc., ROLE_MAP has no entry → kept as-is
        changes2 = self._run(db)
        self.assertEqual(changes2, [], "second run should report no changes")
        row = query_one(db, "SELECT role FROM users WHERE email = 'p@x.com'")
        self.assertEqual(row["role"], "mother")

    # -- Rollback on failure --------------------------------------------------

    def test_failed_migration_rolls_back_the_database(self):
        """If the migration raises, the original table must be untouched."""
        db = _make_old_db()
        _seed(db, [("ok@x.com", "OK", "patient")])

        # Inject a row with an unmappable role that the new CHECK rejects.
        # We bypass the CHECK by temporarily disabling it.
        db.execute("PRAGMA ignore_check_constraints = ON")
        db.execute(
            "INSERT INTO users (email, name, role, password_hash) VALUES ('bad@x.com', 'Bad', 'superuser', 'x:y')"
        )
        db.execute("PRAGMA ignore_check_constraints = OFF")

        with self.assertRaises(Exception):
            # This should fail because 'superuser' is not in the new CHECK list.
            db.execute("PRAGMA foreign_keys = OFF")
            with transaction(db):
                migrate(db)

        # The original table must still be there with the original data.
        rows = query_all(db, "SELECT email FROM users ORDER BY email")
        emails = [r["email"] for r in rows]
        self.assertIn("ok@x.com", emails)
        self.assertIn("bad@x.com", emails)

    # -- ROLE_MAP completeness ------------------------------------------------

    def test_role_map_covers_all_original_roles(self):
        original = {"patient", "provider", "nurse", "caretaker", "admin"}
        self.assertEqual(set(ROLE_MAP.keys()), original,
                         "ROLE_MAP must have an entry for every original role")

    def test_role_map_target_roles_are_all_in_new_role_set(self):
        from migrate_roles import NEW_ROLES
        for old, new in ROLE_MAP.items():
            self.assertIn(new, NEW_ROLES,
                          f"ROLE_MAP maps '{old}' to '{new}' which is not in NEW_ROLES")


class TestMigrationOnSeedData(unittest.TestCase):
    """Run the migration on a database seeded with the actual seed-users.json.
    The setUp creates the *old* schema (with the legacy CHECK) but maps new
    seed roles back to their legacy equivalents so the INSERT succeeds.
    The migration must then convert them back to the new roles."""

    # Map new seed roles → old roles for the pre-migration table
    _TO_OLD = {
        "mother":    "patient",
        "bhw":       "nurse",       # any old role that fits the old CHECK
        "midwife":   "nurse",
        "physician": "provider",
        "rhu_admin": "admin",
        "sysadmin":  "admin",
        # already-old roles pass through
        "patient": "patient", "provider": "provider",
        "nurse": "nurse", "caretaker": "caretaker", "admin": "admin",
    }

    def setUp(self):
        import json
        from pathlib import Path
        from users import hash_password
        import secrets

        seed_path = ROOT / "server" / "seed-users.json"
        seed = json.loads(seed_path.read_text(encoding="utf-8"))

        self.db = open_connection(":memory:")
        self.db.execute("PRAGMA foreign_keys = OFF")
        # Create old schema (original CHECK constraint)
        self.db.execute("""
            CREATE TABLE users (
              id            INTEGER PRIMARY KEY AUTOINCREMENT,
              email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
              name          TEXT NOT NULL,
              role          TEXT NOT NULL CHECK (role IN ('patient','provider','nurse','caretaker','admin')),
              password_hash TEXT NOT NULL,
              status        TEXT NOT NULL DEFAULT 'active',
              created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        for user in seed:
            old_role = self._TO_OLD.get(user["role"], "patient")
            self.db.execute(
                "INSERT INTO users (email, name, role, password_hash) VALUES (?, ?, ?, ?)",
                (user["email"], user["name"], old_role,
                 hash_password(secrets.token_hex(8))),
            )
        self._seed_count = len(seed)

    def test_migration_runs_on_seed_data_without_error(self):
        self.db.execute("PRAGMA foreign_keys = OFF")
        with transaction(self.db):
            changes = migrate(self.db)
        self.assertIsInstance(changes, list)

    def test_no_rows_are_lost_after_migration(self):
        self.db.execute("PRAGMA foreign_keys = OFF")
        with transaction(self.db):
            migrate(self.db)
        count = query_one(self.db, "SELECT COUNT(*) AS n FROM users")["n"]
        self.assertEqual(count, self._seed_count)

    def test_no_legacy_roles_remain_after_migration(self):
        self.db.execute("PRAGMA foreign_keys = OFF")
        with transaction(self.db):
            migrate(self.db)
        old_roles = query_all(
            self.db,
            "SELECT role FROM users WHERE role IN ('patient','provider','nurse','admin')"
        )
        self.assertEqual(old_roles, [],
                         "No legacy roles should remain after migration")


if __name__ == "__main__":
    unittest.main()
