"""Task 9.4: every monthly report export is written to the audit log.

Uses an in-memory database, so data/caap.db is never touched."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

from app import create_app  # noqa: E402
from db import open_database  # noqa: E402

PASSWORD = "test-only-report-pass"


class ReportExportAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = open_database(":memory:", shared_password=PASSWORD)
        cls.app = create_app(cls.db, log=lambda *_: None)

    def call(self, method, path, token=None):
        headers = {"authorization": "Bearer " + token} if token else {}
        status, content_type, data, _ = self.app.handle(method, path, headers, b"")
        payload = json.loads(data) if content_type and content_type.startswith("application/json") else data
        return status, payload

    def sign_in(self, email):
        headers = {"content-type": "application/json"}
        status, _, data, _ = self.app.handle(
            "POST", "/api/login", headers, json.dumps({"email": email, "password": PASSWORD}).encode("utf-8")
        )
        self.assertEqual(status, 200, f"could not sign in as {email}")
        return json.loads(data)["token"]

    def test_export_is_recorded_with_the_month_and_the_exporter(self):
        rhu_token = self.sign_in("rhu.admin@rhu.example")
        status, payload = self.call("GET", "/api/reports/monthly?year=2026&month=11", token=rhu_token)
        self.assertEqual(status, 200)
        self.assertEqual(payload["report"]["month"], 11)

        admin_token = self.sign_in("admin@example.com")
        status, audit = self.call("GET", "/api/audit", token=admin_token)
        self.assertEqual(status, 200)
        exports = [event for event in audit["events"] if event["action"] == "report_exported"]
        self.assertTrue(exports, "the export should be in the audit log")
        self.assertEqual(exports[0]["detail"], "2026-11")
        self.assertEqual(exports[0]["user_email"], "rhu.admin@rhu.example")

    def test_refused_export_is_not_recorded(self):
        bhw_token = self.sign_in("b.reyes@bhw.example")
        status, _ = self.call("GET", "/api/reports/monthly?year=2026&month=11", token=bhw_token)
        self.assertEqual(status, 403)
        admin_token = self.sign_in("admin@example.com")
        _, audit = self.call("GET", "/api/audit", token=admin_token)
        self.assertFalse(
            any(event["action"] == "report_exported" and event["user_email"] == "b.reyes@bhw.example"
                for event in audit["events"])
        )


if __name__ == "__main__":
    unittest.main()
