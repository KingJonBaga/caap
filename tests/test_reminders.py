"""Tests for Phase 7, task 7.1: reminders table and in_app channel.

Covers:
  - POST /api/reminders  with channel='in_app' succeeds (201)
  - POST /api/reminders  with invalid channel returns 400
  - POST /api/reminders  missing 'message' returns 400
  - GET  /api/reminders  returns 200 with list
  - GET  /api/reminders  unauthenticated returns 401
  - POST /api/reminders  by sysadmin returns 403
  - GET  /api/reminders  by sysadmin returns 403
  - send_in_app marks the reminder as sent

Run with: py -m unittest tests/test_reminders.py -v
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

from app import create_app      # noqa: E402
from db  import open_database   # noqa: E402

STAFF_PASSWORD = "test-only-7.1-pass"


# ── Thin test helpers ─────────────────────────────────────────────────────────

class Result:
    def __init__(self, status, content_type, body, headers):
        self.status = status
        self.data = json.loads(body) if content_type and "json" in content_type else body


class Client:
    def __init__(self, app):
        self.app = app

    def request(self, method, path, body=None, token=None):
        headers = {}
        raw = b""
        if body is not None:
            raw = json.dumps(body).encode()
            headers["content-type"] = "application/json"
        if token:
            headers["authorization"] = "Bearer " + token
        return Result(*self.app.handle(method, path, headers, raw))

    def sign_in(self, email, password):
        res = self.request("POST", "/api/login",
                           body={"email": email, "password": password})
        assert res.status == 200, f"login failed for {email}: {res.data}"
        return res.data["token"]


# ── Test class ────────────────────────────────────────────────────────────────

class ReminderEndpointTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db  = open_database(":memory:", shared_password=STAFF_PASSWORD)
        cls.app = create_app(cls.db, log=lambda *a: None)
        cls.cli = Client(cls.app)

        cls.tok = {
            "bhw":       cls.cli.sign_in("b.reyes@bhw.example",   STAFF_PASSWORD),
            "midwife":   cls.cli.sign_in("c.lim@rhu.example",     STAFF_PASSWORD),
            "physician": cls.cli.sign_in("a.okafor@rhu.example",  STAFF_PASSWORD),
            "rhu_admin": cls.cli.sign_in("rhu.admin@rhu.example", STAFF_PASSWORD),
            "sysadmin":  cls.cli.sign_in("admin@example.com",     STAFF_PASSWORD),
        }

    def req(self, method, path, **kwargs):
        return self.cli.request(method, path, **kwargs)

    def _base_body(self, **overrides):
        body = {
            "message":  "Your prenatal visit is due tomorrow.",
            "send_at":  "2026-12-01T08:00:00Z",
            "channel":  "in_app",
            "user_id":  1,
        }
        body.update(overrides)
        return body

    # ── POST /api/reminders ───────────────────────────────────────────────────

    def test_create_in_app_reminder_returns_201(self):
        """Creating a reminder with channel='in_app' must succeed."""
        res = self.req("POST", "/api/reminders",
                       token=self.tok["bhw"],
                       body=self._base_body())
        self.assertEqual(res.status, 201, res.data)
        reminder = res.data["reminder"]
        self.assertEqual(reminder["channel"], "in_app")
        self.assertIsNotNone(reminder["id"])
        self.assertIsNone(reminder["sent_at"])

    def test_create_with_invalid_channel_returns_400(self):
        """An unrecognised channel value must be refused."""
        res = self.req("POST", "/api/reminders",
                       token=self.tok["bhw"],
                       body=self._base_body(channel="push_notification"))
        self.assertEqual(res.status, 400, res.data)
        self.assertIn("channel", res.data.get("error", "").lower())

    def test_create_missing_message_returns_400(self):
        """A reminder without a message must be refused."""
        body = self._base_body()
        del body["message"]
        res = self.req("POST", "/api/reminders",
                       token=self.tok["bhw"],
                       body=body)
        self.assertEqual(res.status, 400, res.data)
        self.assertIn("message", res.data.get("error", "").lower())

    def test_create_missing_send_at_returns_400(self):
        """A reminder without send_at must be refused."""
        body = self._base_body()
        del body["send_at"]
        res = self.req("POST", "/api/reminders",
                       token=self.tok["bhw"],
                       body=body)
        self.assertEqual(res.status, 400, res.data)

    def test_create_missing_channel_returns_400(self):
        """A reminder without a channel must be refused."""
        body = self._base_body()
        del body["channel"]
        res = self.req("POST", "/api/reminders",
                       token=self.tok["bhw"],
                       body=body)
        self.assertEqual(res.status, 400, res.data)

    def test_create_missing_both_ids_returns_400(self):
        """A reminder with neither mother_id nor user_id must be refused."""
        body = {
            "message": "Test",
            "send_at": "2026-12-01T08:00:00Z",
            "channel": "in_app",
        }
        res = self.req("POST", "/api/reminders",
                       token=self.tok["bhw"],
                       body=body)
        self.assertEqual(res.status, 400, res.data)

    def test_sysadmin_cannot_create_reminder_returns_403(self):
        """sysadmin is not in REMINDER_ROLES."""
        res = self.req("POST", "/api/reminders",
                       token=self.tok["sysadmin"],
                       body=self._base_body())
        self.assertEqual(res.status, 403, res.data)

    def test_unauthenticated_create_returns_401(self):
        res = self.req("POST", "/api/reminders", body=self._base_body())
        self.assertEqual(res.status, 401)

    # ── GET /api/reminders ────────────────────────────────────────────────────

    def test_list_reminders_returns_200_with_list(self):
        # Seed at least one reminder first
        self.req("POST", "/api/reminders",
                 token=self.tok["midwife"],
                 body=self._base_body())
        res = self.req("GET", "/api/reminders", token=self.tok["midwife"])
        self.assertEqual(res.status, 200, res.data)
        self.assertIn("reminders", res.data)
        self.assertIsInstance(res.data["reminders"], list)

    def test_list_unauthenticated_returns_401(self):
        res = self.req("GET", "/api/reminders")
        self.assertEqual(res.status, 401)

    def test_sysadmin_cannot_list_reminders_returns_403(self):
        """sysadmin is not in REMINDER_ROLES."""
        res = self.req("GET", "/api/reminders", token=self.tok["sysadmin"])
        self.assertEqual(res.status, 403, res.data)

    def test_physician_can_list_reminders(self):
        res = self.req("GET", "/api/reminders", token=self.tok["physician"])
        self.assertEqual(res.status, 200, res.data)

    def test_rhu_admin_can_list_reminders(self):
        res = self.req("GET", "/api/reminders", token=self.tok["rhu_admin"])
        self.assertEqual(res.status, 200, res.data)

    # ── send_in_app marks the reminder as sent ─────────────────────────────────

    def test_send_in_app_marks_sent(self):
        """send_in_app(db, reminder_id) must set sent_at to a non-None value."""
        import reminders as reminders_module

        result = reminders_module.create_reminder(self.db, {
            "message":  "Test in-app reminder",
            "send_at":  "2026-12-01T08:00:00Z",
            "channel":  "in_app",
            "user_id":  1,
        })
        self.assertIn("reminder", result, result)
        rid = result["reminder"]["id"]
        self.assertIsNone(result["reminder"]["sent_at"])

        sent_result = reminders_module.send_in_app(self.db, rid)
        self.assertIn("reminder", sent_result, sent_result)
        self.assertIsNotNone(sent_result["reminder"]["sent_at"],
                             "sent_at must be set after send_in_app")

    def test_send_in_app_on_nonexistent_id_returns_404(self):
        import reminders as reminders_module
        result = reminders_module.send_in_app(self.db, 999999)
        self.assertEqual(result.get("status"), 404)

    def test_send_in_app_on_sms_channel_returns_400(self):
        """send_in_app must refuse a reminder whose channel is 'sms'."""
        import reminders as reminders_module

        result = reminders_module.create_reminder(self.db, {
            "message": "SMS reminder",
            "send_at": "2026-12-02T08:00:00Z",
            "channel": "sms",
            "user_id": 1,
        })
        self.assertIn("reminder", result, result)
        rid = result["reminder"]["id"]

        err = reminders_module.send_in_app(self.db, rid)
        self.assertEqual(err.get("status"), 400)

    # ── mark_failed records the reason ────────────────────────────────────────

    def test_mark_failed_records_reason(self):
        import reminders as reminders_module

        result = reminders_module.create_reminder(self.db, {
            "message": "Will fail",
            "send_at": "2026-12-03T08:00:00Z",
            "channel": "in_app",
            "user_id": 1,
        })
        self.assertIn("reminder", result)
        rid = result["reminder"]["id"]

        updated = reminders_module.mark_failed(self.db, rid, "Network timeout")
        self.assertEqual(updated["failed_reason"], "Network timeout")

    # ── Reminders table created by setup_reminders ────────────────────────────

    def test_reminders_table_exists(self):
        from common import query_one
        row = query_one(
            self.db,
            "SELECT name FROM sqlite_master WHERE type='table' AND name='reminders'",
        )
        self.assertIsNotNone(row, "reminders table must exist in the database")


if __name__ == "__main__":
    unittest.main()
