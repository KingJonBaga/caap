"""Task 9.3: small groups in the monthly report are shown as "<5", not as numbers.

Uses an in-memory database, so data/caap.db is never touched."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import reports  # noqa: E402
from db import open_database  # noqa: E402


class SuppressSmallTests(unittest.TestCase):
    def test_counts_from_one_to_four_are_hidden(self):
        for count in range(1, reports.MIN_GROUP_SIZE):
            self.assertEqual(reports.suppress_small(count), "<5")

    def test_five_and_above_are_shown(self):
        self.assertEqual(reports.suppress_small(reports.MIN_GROUP_SIZE), reports.MIN_GROUP_SIZE)
        self.assertEqual(reports.suppress_small(42), 42)

    def test_zero_is_shown_as_zero(self):
        self.assertEqual(reports.suppress_small(0), 0)


class MonthlyReportTests(unittest.TestCase):
    def test_empty_month_has_zero_counts_and_no_names(self):
        db = open_database(":memory:", shared_password="test-only-pass")
        report = reports.monthly_report(db, 2026, 11)
        counts = {k: v for k, v in report.items() if k not in ("year", "month")}
        self.assertTrue(all(value == 0 for value in counts.values()))
        self.assertEqual(report["year"], 2026)
        self.assertEqual(report["month"], 11)
        for key in ("name", "email", "contact", "address", "dateOfBirth"):
            self.assertNotIn(key, report)


if __name__ == "__main__":
    unittest.main()
