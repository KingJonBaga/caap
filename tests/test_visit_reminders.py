"""Tests for task 7.2: a reminder is created when a visit is scheduled.

Checks:
  - Scheduling a prenatal visit creates a reminder row in the reminders table.
  - Scheduling a postnatal visit creates a reminder row.
  - Scheduling a home visit creates a reminder row.
  - Every auto-created reminder uses the in_app channel.
  - The reminder's send_at matches the visit's scheduled_for.

Run with: py -m unittest tests/test_visit_reminders.py -v
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAFF_PASSWORD = "test-only-staff-pass"
sys.path.insert(0, str(ROOT / "server"))

from common import query_all  # noqa: E402
from db import open_database  # noqa: E402
import visit_records as vr    # noqa: E402


CONSENT  = "2026-10-01T08:00:00Z"
FUTURE_P = "2099-07-15T10:00:00"
FUTURE_O = "2099-08-20T11:00:00"
FUTURE_H = "2099-09-05T08:30:00"


class VisitReminderTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db = open_database(":memory:", shared_password=STAFF_PASSWORD)

        # Resolve user IDs from the seed data
        from common import query_one
        bhw = query_one(cls.db, "SELECT * FROM users WHERE role = 'bhw' LIMIT 1")
        mid = query_one(cls.db, "SELECT * FROM users WHERE role = 'midwife' LIMIT 1")
        cls.bhw_user     = dict(bhw)
        cls.midwife_user = dict(mid)

        # Register a mother
        import mothers as mothers_module
        result = mothers_module.create_mother(
            cls.db,
            cls.midwife_user,
            {
                "name":             "Reminder Test Mother",
                "dateOfBirth":      "1991-03-10",
                "consentRecordedAt": CONSENT,
            },
        )
        assert "mother" in result, f"mother setup failed: {result}"
        cls.mother_id = result["mother"]["id"]

    def _reminders_for_mother(self):
        return query_all(
            self.db,
            "SELECT * FROM reminders WHERE mother_id = ?",
            (self.mother_id,),
        )

    # ── Prenatal visit ────────────────────────────────────────────────────────

    def test_prenatal_visit_creates_reminder(self):
        before = len(self._reminders_for_mother())
        result = vr.create_prenatal(
            self.db, self.midwife_user, self.mother_id,
            {"scheduledFor": FUTURE_P},
        )
        self.assertIn("visit", result, result)

        after = self._reminders_for_mother()
        self.assertEqual(len(after), before + 1, "Expected one new reminder after prenatal visit creation")

    def test_prenatal_reminder_channel_is_in_app(self):
        vr.create_prenatal(
            self.db, self.midwife_user, self.mother_id,
            {"scheduledFor": FUTURE_P},
        )
        reminders = self._reminders_for_mother()
        prenatal_reminders = [r for r in reminders if "Prenatal" in (r["message"] or "")]
        self.assertTrue(prenatal_reminders, "No prenatal reminder found")
        for r in prenatal_reminders:
            self.assertEqual(r["channel"], "in_app")

    def test_prenatal_reminder_send_at_matches_scheduled_for(self):
        result = vr.create_prenatal(
            self.db, self.midwife_user, self.mother_id,
            {"scheduledFor": FUTURE_P},
        )
        visit = result["visit"]
        reminders = self._reminders_for_mother()
        matched = [r for r in reminders if r["send_at"] == visit["scheduledFor"]]
        self.assertTrue(matched, f"No reminder with send_at={visit['scheduledFor']!r}")

    # ── Postnatal visit ───────────────────────────────────────────────────────

    def test_postnatal_visit_creates_reminder(self):
        before = len(self._reminders_for_mother())
        result = vr.create_postnatal(
            self.db, self.midwife_user, self.mother_id,
            {"scheduledFor": FUTURE_O},
        )
        self.assertIn("visit", result, result)

        after = self._reminders_for_mother()
        self.assertEqual(len(after), before + 1, "Expected one new reminder after postnatal visit creation")

    def test_postnatal_reminder_channel_is_in_app(self):
        vr.create_postnatal(
            self.db, self.midwife_user, self.mother_id,
            {"scheduledFor": FUTURE_O},
        )
        reminders = self._reminders_for_mother()
        postnatal_reminders = [r for r in reminders if "Postnatal" in (r["message"] or "")]
        self.assertTrue(postnatal_reminders, "No postnatal reminder found")
        for r in postnatal_reminders:
            self.assertEqual(r["channel"], "in_app")

    def test_postnatal_reminder_send_at_matches_scheduled_for(self):
        result = vr.create_postnatal(
            self.db, self.midwife_user, self.mother_id,
            {"scheduledFor": FUTURE_O},
        )
        visit = result["visit"]
        reminders = self._reminders_for_mother()
        matched = [r for r in reminders if r["send_at"] == visit["scheduledFor"]]
        self.assertTrue(matched, f"No reminder with send_at={visit['scheduledFor']!r}")

    # ── Home visit ────────────────────────────────────────────────────────────

    def test_home_visit_creates_reminder(self):
        before = len(self._reminders_for_mother())
        result = vr.create_home_visit(
            self.db, self.bhw_user, self.mother_id,
            {"scheduledFor": FUTURE_H},
        )
        self.assertIn("visit", result, result)

        after = self._reminders_for_mother()
        self.assertEqual(len(after), before + 1, "Expected one new reminder after home visit creation")

    def test_home_visit_reminder_channel_is_in_app(self):
        vr.create_home_visit(
            self.db, self.bhw_user, self.mother_id,
            {"scheduledFor": FUTURE_H},
        )
        reminders = self._reminders_for_mother()
        home_reminders = [r for r in reminders if "Home visit" in (r["message"] or "")]
        self.assertTrue(home_reminders, "No home visit reminder found")
        for r in home_reminders:
            self.assertEqual(r["channel"], "in_app")

    def test_home_visit_reminder_send_at_matches_scheduled_for(self):
        result = vr.create_home_visit(
            self.db, self.bhw_user, self.mother_id,
            {"scheduledFor": FUTURE_H},
        )
        visit = result["visit"]
        reminders = self._reminders_for_mother()
        matched = [r for r in reminders if r["send_at"] == visit["scheduledFor"]]
        self.assertTrue(matched, f"No reminder with send_at={visit['scheduledFor']!r}")


if __name__ == "__main__":
    unittest.main()
