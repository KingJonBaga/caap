"""Task 9.1: the RHU dashboard numbers come from the database and contain no personal details.

Uses an in-memory database, so data/caap.db is never touched."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

from app import create_app  # noqa: E402
from db import open_database  # noqa: E402

PASSWORD = "test-only-rhu-pass"


class RhuDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = open_database(":memory:", shared_password=PASSWORD)
        cls.app = create_app(cls.db, log=lambda *_: None)

    def call(self, method, path, body=None, token=None):
        headers = {}
        raw = b""
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            headers["content-type"] = "application/json"
        if token:
            headers["authorization"] = "Bearer " + token
        status, content_type, data, _ = self.app.handle(method, path, headers, raw)
        payload = json.loads(data) if content_type and content_type.startswith("application/json") else data
        return status, payload

    def sign_in(self, email):
        status, payload = self.call("POST", "/api/login", {"email": email, "password": PASSWORD})
        self.assertEqual(status, 200, f"could not sign in as {email}")
        return payload["token"]

    def test_rhu_admin_gets_live_counts_with_expected_fields(self):
        token = self.sign_in("rhu.admin@rhu.example")
        status, data = self.call("GET", "/api/rhu/dashboard", token=token)
        self.assertEqual(status, 200)
        for key in ("registeredPrenatal", "registeredPostpartum", "underPrenatalMonitoring",
                    "postpartumFollowUp", "openFlags", "overdueReferrals", "referralsByStatus"):
            self.assertIn(key, data)
        self.assertIsInstance(data["referralsByStatus"], dict)

    def test_no_names_or_contact_details_in_the_dashboard(self):
        token = self.sign_in("rhu.admin@rhu.example")
        _, data = self.call("GET", "/api/rhu/dashboard", token=token)
        text = json.dumps(data).lower()
        for personal in ("name", "email", "contact", "address", "date_of_birth", "phone"):
            self.assertNotIn(personal, text)

    def test_other_roles_are_refused(self):
        bhw_token = self.sign_in("b.reyes@bhw.example")
        status, _ = self.call("GET", "/api/rhu/dashboard", token=bhw_token)
        self.assertEqual(status, 403)
        status, _ = self.call("GET", "/api/rhu/dashboard")
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
