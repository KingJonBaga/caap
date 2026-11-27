"""Task 9.2: the monthly report counts activity and never includes names or contact details.

The test fills a temporary in-memory database with a mother whose name, contact and address are
known strings, then checks none of those strings appear in the report. data/caap.db is never used."""
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

from app import create_app  # noqa: E402
from db import open_database  # noqa: E402

PASSWORD = "test-only-report-pass"
MOTHER_NAME = "Ysabel Dela Cruz Test"
MOTHER_CONTACT = "0917-555-0199"
MOTHER_ADDRESS = "Purok 9 Sample Street"
MOTHER_DOB = "1994-03-14"


class MonthlyReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = open_database(":memory:", shared_password=PASSWORD)
        cls.app = create_app(cls.db, log=lambda *_: None)
        cls.bhw = cls.sign_in("b.reyes@bhw.example")
        cls.rhu = cls.sign_in("rhu.admin@rhu.example")
        now = datetime.now(timezone.utc)
        cls.year, cls.month = now.year, now.month
        # Register a mother this month, with consent recorded, so the report has something to count.
        status, _, data, _ = cls.app.handle(
            "POST", "/api/mothers",
            {"content-type": "application/json", "authorization": "Bearer " + cls.bhw},
            json.dumps({
                "name": MOTHER_NAME,
                "dateOfBirth": MOTHER_DOB,
                "contact": MOTHER_CONTACT,
                "addressArea": MOTHER_ADDRESS,
                "consentRecordedAt": f"{cls.year:04d}-{cls.month:02d}-01 08:00:00",
            }).encode("utf-8"),
        )
        assert status == 201, data

    @classmethod
    def sign_in(cls, email):
        status, _, data, _ = cls.app.handle(
            "POST", "/api/login", {"content-type": "application/json"},
            json.dumps({"email": email, "password": PASSWORD}).encode("utf-8"),
        )
        assert status == 200, email
        return json.loads(data)["token"]

    def report(self, token, year=None, month=None):
        year = year or self.year
        month = month or self.month
        status, _, data, _ = self.app.handle(
            "GET", f"/api/reports/monthly?year={year}&month={month}",
            {"authorization": "Bearer " + token}, b"",
        )
        return status, json.loads(data)

    def test_report_counts_the_new_mother_without_naming_her(self):
        status, payload = self.report(self.rhu)
        self.assertEqual(status, 200)
        report = payload["report"]
        self.assertEqual(report["newMothers"], "<5", "one mother is below the minimum group size")

    def test_no_names_contacts_or_addresses_appear_anywhere_in_the_report(self):
        _, payload = self.report(self.rhu)
        text = json.dumps(payload)
        for personal in (MOTHER_NAME, "Ysabel", "Dela Cruz", MOTHER_CONTACT, MOTHER_ADDRESS, MOTHER_DOB):
            self.assertNotIn(personal, text)

    def test_report_keys_are_counts_only(self):
        _, payload = self.report(self.rhu)
        report = payload["report"]
        for key in ("newMothers", "prenatalCompleted", "postnatalCompleted", "homeVisitsCompleted",
                    "riskFlagsRaised", "referralsCreated", "referralsClosed"):
            self.assertIn(key, report)
            self.assertTrue(isinstance(report[key], int) or report[key] == "<5", key)
        for forbidden in ("name", "contact", "address", "dateOfBirth", "email"):
            self.assertNotIn(forbidden, report)

    def test_report_is_refused_to_roles_outside_rhu_and_clinical_care(self):
        status, _ = self.report(self.bhw)
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
