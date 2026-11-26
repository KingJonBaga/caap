"""Tests for Phase 5, task 5.1: risk_indicators and risk_flags table creation.

Covers:
  - Both tables are created with the expected columns.
  - risk_indicators starts with zero rows (table is seeded empty).
  - risk_flags enforces the status CHECK constraint (open / reviewed / dismissed).

Run with: py -m unittest discover -s tests
"""
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

from db import open_database  # noqa: E402


def _columns(db, table):
    """Return a set of column names for *table*."""
    rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


class RiskIndicatorsTableTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db = open_database(":memory:", shared_password="test-pass")

    # ── risk_indicators ───────────────────────────────────────────────────────

    def test_risk_indicators_table_exists(self):
        row = self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='risk_indicators'"
        ).fetchone()
        self.assertIsNotNone(row, "risk_indicators table should exist")

    def test_risk_indicators_has_expected_columns(self):
        expected = {
            "id", "code", "description", "source_reference",
            "rule", "approved_by", "approved_at", "active",
            "created_at", "created_by", "updated_at",
        }
        actual = _columns(self.db, "risk_indicators")
        self.assertTrue(
            expected.issubset(actual),
            f"Missing columns: {expected - actual}",
        )

    def test_risk_indicators_starts_empty(self):
        count = self.db.execute("SELECT COUNT(*) FROM risk_indicators").fetchone()[0]
        self.assertEqual(count, 0, "risk_indicators must start with zero rows")

    # ── risk_flags ────────────────────────────────────────────────────────────

    def test_risk_flags_table_exists(self):
        row = self.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='risk_flags'"
        ).fetchone()
        self.assertIsNotNone(row, "risk_flags table should exist")

    def test_risk_flags_has_expected_columns(self):
        expected = {
            "id", "mother_id", "indicator_id", "observation",
            "raised_by", "raised_at", "status",
            "reviewed_by", "review_note",
            "created_at", "created_by", "updated_at",
        }
        actual = _columns(self.db, "risk_flags")
        self.assertTrue(
            expected.issubset(actual),
            f"Missing columns: {expected - actual}",
        )

    def test_risk_flags_status_check_rejects_invalid_value(self):
        """Inserting a row with an unknown status must raise an IntegrityError."""
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """INSERT INTO risk_flags
                   (mother_id, raised_by, status, created_by)
                   VALUES (999, 999, 'invalid_status', 999)"""
            )
            self.db.commit()

    def test_risk_flags_status_open_is_accepted(self):
        """'open' is a valid status and should not raise."""
        try:
            self.db.execute(
                """INSERT INTO risk_flags
                   (mother_id, raised_by, status, created_by)
                   VALUES (999, 999, 'open', 999)"""
            )
            # Roll back so we don't leave a dangling FK-violating row
            self.db.execute("ROLLBACK")
        except sqlite3.IntegrityError as exc:
            # FK violation is fine here (mother 999 doesn't exist and FK enforcement
            # may be off for in-memory DBs); a CHECK violation on status is not.
            if "CHECK" in str(exc):
                self.fail(f"'open' should be a valid status but got: {exc}")

    def test_risk_flags_status_reviewed_is_accepted(self):
        try:
            self.db.execute(
                """INSERT INTO risk_flags
                   (mother_id, raised_by, status, created_by)
                   VALUES (999, 999, 'reviewed', 999)"""
            )
            self.db.execute("ROLLBACK")
        except sqlite3.IntegrityError as exc:
            if "CHECK" in str(exc):
                self.fail(f"'reviewed' should be a valid status but got: {exc}")

    def test_risk_flags_status_dismissed_is_accepted(self):
        try:
            self.db.execute(
                """INSERT INTO risk_flags
                   (mother_id, raised_by, status, created_by)
                   VALUES (999, 999, 'dismissed', 999)"""
            )
            self.db.execute("ROLLBACK")
        except sqlite3.IntegrityError as exc:
            if "CHECK" in str(exc):
                self.fail(f"'dismissed' should be a valid status but got: {exc}")


if __name__ == "__main__":
    unittest.main()
