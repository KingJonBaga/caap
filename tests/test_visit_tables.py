"""Tests for task 3.1: prenatal_visits, postnatal_visits and home_visits table schema.

Verifies:
- The three tables are created by setup_visit_records(db).
- Each table has the required columns.
- The status CHECK constraints reject invalid values.
- Required columns enforce NOT NULL.

Run with: py -m unittest discover -s tests
"""
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

from common import open_connection
from visit_records import setup_visit_records
import mothers as mothers_module
import users


STAFF_PASSWORD = "test-only-staff-pass"


def _make_db():
    """Return a fresh in-memory database with users, mothers and visit tables set up."""
    db = open_connection(":memory:")
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS patients (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, display_id TEXT NOT NULL,
          stage TEXT NOT NULL DEFAULT '', gestation TEXT NOT NULL DEFAULT '',
          blood_pressure TEXT NOT NULL DEFAULT '', weight TEXT NOT NULL DEFAULT '',
          update_text TEXT NOT NULL DEFAULT '', appointment TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT '', flag TEXT NOT NULL DEFAULT '',
          note TEXT NOT NULL DEFAULT '', history TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    users.setup_users(db, shared_password=STAFF_PASSWORD, logins_file=None)
    mothers_module.setup_mothers(db)
    setup_visit_records(db)
    return db


def _table_columns(db, table):
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}


def _user_id(db, email):
    row = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    return row[0] if row else None


def _mother_id(db, bhw_id):
    """Insert a minimal mother record and return its id."""
    cur = db.execute(
        """INSERT INTO mothers
           (name, consent_recorded_at, created_by, duplicate_key)
           VALUES ('Test Mother', '2025-01-01T00:00:00Z', ?, 'unique-test-key-' || abs(random()))""",
        (bhw_id,),
    )
    return cur.lastrowid


class TestTablesExist(unittest.TestCase):
    """The three tables are created by setup_visit_records."""

    @classmethod
    def setUpClass(cls):
        cls.db = _make_db()

    def _table_exists(self, name):
        row = self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None

    def test_prenatal_visits_table_exists(self):
        self.assertTrue(self._table_exists("prenatal_visits"))

    def test_postnatal_visits_table_exists(self):
        self.assertTrue(self._table_exists("postnatal_visits"))

    def test_home_visits_table_exists(self):
        self.assertTrue(self._table_exists("home_visits"))


class TestPrenatalVisitsColumns(unittest.TestCase):
    """prenatal_visits has all required columns."""

    REQUIRED = {
        "id", "mother_id", "scheduled_for", "status",
        "performed_by", "vitals", "observations",
        "created_by", "created_at", "updated_at",
    }

    @classmethod
    def setUpClass(cls):
        cls.db = _make_db()
        cls.columns = _table_columns(cls.db, "prenatal_visits")

    def test_all_required_columns_present(self):
        missing = self.REQUIRED - self.columns
        self.assertFalse(missing, f"Missing columns in prenatal_visits: {missing}")


class TestPostnatalVisitsColumns(unittest.TestCase):
    """postnatal_visits has all required columns."""

    REQUIRED = {
        "id", "mother_id", "scheduled_for", "status",
        "performed_by", "vitals", "observations",
        "created_by", "created_at", "updated_at",
    }

    @classmethod
    def setUpClass(cls):
        cls.db = _make_db()
        cls.columns = _table_columns(cls.db, "postnatal_visits")

    def test_all_required_columns_present(self):
        missing = self.REQUIRED - self.columns
        self.assertFalse(missing, f"Missing columns in postnatal_visits: {missing}")


class TestHomeVisitsColumns(unittest.TestCase):
    """home_visits has all required columns."""

    REQUIRED = {
        "id", "mother_id", "bhw_id", "scheduled_for", "status",
        "checklist_id", "checklist_answers", "notes",
        "submitted_to_rhu_at", "created_at", "updated_at",
    }

    @classmethod
    def setUpClass(cls):
        cls.db = _make_db()
        cls.columns = _table_columns(cls.db, "home_visits")

    def test_all_required_columns_present(self):
        missing = self.REQUIRED - self.columns
        self.assertFalse(missing, f"Missing columns in home_visits: {missing}")


class TestClinicVisitStatusConstraints(unittest.TestCase):
    """prenatal_visits and postnatal_visits enforce their status CHECK constraint."""

    @classmethod
    def setUpClass(cls):
        cls.db = _make_db()
        bhw_id = _user_id(cls.db, "b.reyes@bhw.example")
        cls.mother_id = _mother_id(cls.db, bhw_id)
        cls.bhw_id = bhw_id

    def _insert_clinic(self, table, status):
        self.db.execute(
            f"""INSERT INTO {table}
               (mother_id, scheduled_for, status, created_by)
               VALUES (?, '2025-06-01', ?, ?)""",
            (self.mother_id, status, self.bhw_id),
        )

    def test_prenatal_valid_statuses_are_accepted(self):
        for status in ("scheduled", "completed", "missed", "rescheduled"):
            with self.subTest(status=status):
                self._insert_clinic("prenatal_visits", status)  # must not raise

    def test_prenatal_invalid_status_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_clinic("prenatal_visits", "pending")

    def test_postnatal_valid_statuses_are_accepted(self):
        for status in ("scheduled", "completed", "missed", "rescheduled"):
            with self.subTest(status=status):
                self._insert_clinic("postnatal_visits", status)  # must not raise

    def test_postnatal_invalid_status_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_clinic("postnatal_visits", "planned")

    def test_prenatal_requires_mother_id(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO prenatal_visits (scheduled_for, created_by) VALUES ('2025-06-01', ?)",
                (self.bhw_id,),
            )

    def test_prenatal_requires_scheduled_for(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO prenatal_visits (mother_id, created_by) VALUES (?, ?)",
                (self.mother_id, self.bhw_id),
            )

    def test_prenatal_requires_created_by(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO prenatal_visits (mother_id, scheduled_for) VALUES (?, '2025-06-01')",
                (self.mother_id,),
            )


class TestHomeVisitStatusConstraints(unittest.TestCase):
    """home_visits enforces its status CHECK constraint."""

    @classmethod
    def setUpClass(cls):
        cls.db = _make_db()
        cls.bhw_id = _user_id(cls.db, "b.reyes@bhw.example")
        cls.mother_id = _mother_id(cls.db, cls.bhw_id)

    def _insert_home(self, status):
        self.db.execute(
            """INSERT INTO home_visits
               (mother_id, bhw_id, scheduled_for, status)
               VALUES (?, ?, '2025-06-01', ?)""",
            (self.mother_id, self.bhw_id, status),
        )

    def test_valid_statuses_are_accepted(self):
        for status in ("planned", "completed", "missed", "rescheduled"):
            with self.subTest(status=status):
                self._insert_home(status)  # must not raise

    def test_invalid_status_is_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._insert_home("scheduled")  # 'scheduled' is a clinic status, not valid here

    def test_requires_mother_id(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO home_visits (bhw_id, scheduled_for) VALUES (?, '2025-06-01')",
                (self.bhw_id,),
            )

    def test_requires_bhw_id(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO home_visits (mother_id, scheduled_for) VALUES (?, '2025-06-01')",
                (self.mother_id,),
            )

    def test_requires_scheduled_for(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO home_visits (mother_id, bhw_id) VALUES (?, ?)",
                (self.mother_id, self.bhw_id),
            )


class TestSetupIsIdempotent(unittest.TestCase):
    """Calling setup_visit_records twice must not raise (IF NOT EXISTS)."""

    def test_double_setup_does_not_raise(self):
        db = _make_db()
        setup_visit_records(db)  # second call


if __name__ == "__main__":
    unittest.main()
