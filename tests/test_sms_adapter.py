"""Tests for task 7.3: SMS adapter interface.

Covers:
  - send_sms with a non-sms channel returns 400
  - send_sms on a nonexistent reminder returns 404
  - send_sms with the default (null) gateway stores failed_reason
  - send_sms with a mock gateway that succeeds marks sent_at
  - SmsGateway can be replaced via duck-typing or subclass

Run with: py -m unittest tests/test_sms_adapter.py -v
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import sqlite3
from reminders import (
    NullSmsGateway,
    SmsGateway,
    SmsGatewayNotConfigured,
    create_reminder,
    get_sms_gateway,
    mark_failed,
    send_sms,
    set_sms_gateway,
    setup_reminders,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_db():
    """Return an in-memory SQLite database with the reminders table set up."""
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.row_factory = sqlite3.Row
    # reminders references mothers(id) and users(id); create stubs so FK
    # constraints don't prevent inserts (FK enforcement is off by default in SQLite).
    db.execute("""
        CREATE TABLE IF NOT EXISTS mothers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT
        )
    """)
    setup_reminders(db)
    return db


def _sms_reminder(db, mother_id=1):
    """Create and return a minimal sms reminder."""
    result = create_reminder(db, {
        "mother_id": mother_id,
        "channel": "sms",
        "message": "Your visit is tomorrow.",
        "send_at": "2099-06-01T08:00:00",
    })
    assert "reminder" in result, result
    return result["reminder"]


def _in_app_reminder(db, mother_id=1):
    """Create and return a minimal in_app reminder."""
    result = create_reminder(db, {
        "mother_id": mother_id,
        "channel": "in_app",
        "message": "Your visit is tomorrow.",
        "send_at": "2099-06-01T08:00:00",
    })
    assert "reminder" in result, result
    return result["reminder"]


# ── A mock gateway that succeeds ──────────────────────────────────────────────

class SucceedingGateway(SmsGateway):
    """Gateway that records calls and always succeeds."""

    def __init__(self):
        self.calls = []

    def send(self, to, message):
        self.calls.append({"to": to, "message": message})


class FailingGateway(SmsGateway):
    """Gateway that always raises a descriptive error."""

    def send(self, to, message):
        raise RuntimeError("network unreachable")


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestSmsAdapterInterface(unittest.TestCase):

    def setUp(self):
        self.db = _make_db()
        # Insert a stub mother row so foreign key values are valid.
        self.db.execute("INSERT INTO mothers (id, name) VALUES (1, 'Test Mother')")

    def tearDown(self):
        # Reset the global gateway after each test so tests don't bleed into each other.
        set_sms_gateway(None)

    # -- channel validation ---------------------------------------------------

    def test_send_sms_wrong_channel_returns_400(self):
        """send_sms on an in_app reminder returns 400."""
        reminder = _in_app_reminder(self.db)
        result = send_sms(self.db, reminder["id"])
        self.assertEqual(result["status"], 400)
        self.assertIn("in_app", result["error"])

    # -- not-found ------------------------------------------------------------

    def test_send_sms_nonexistent_reminder_returns_404(self):
        """send_sms on a reminder that does not exist returns 404."""
        result = send_sms(self.db, 9999)
        self.assertEqual(result["status"], 404)
        self.assertIn("not found", result["error"].lower())

    # -- null / default gateway -----------------------------------------------

    def test_send_sms_null_gateway_stores_failed_reason(self):
        """Default NullSmsGateway records failed_reason on the reminder row."""
        reminder = _sms_reminder(self.db)
        result = send_sms(self.db, reminder["id"])

        self.assertEqual(result["status"], 503)
        self.assertEqual(result["error"], "SMS gateway not configured.")

        # The reminder row must have a non-empty failed_reason.
        updated = result["reminder"]
        self.assertIsNotNone(updated["failed_reason"])
        self.assertTrue(len(updated["failed_reason"]) > 0)

    def test_send_sms_null_gateway_does_not_mark_sent(self):
        """Default NullSmsGateway must NOT set sent_at."""
        reminder = _sms_reminder(self.db)
        result = send_sms(self.db, reminder["id"])

        self.assertIsNone(result["reminder"]["sent_at"])

    # -- successful mock gateway ----------------------------------------------

    def test_send_sms_succeeding_gateway_marks_sent_at(self):
        """A gateway that succeeds causes sent_at to be set."""
        reminder = _sms_reminder(self.db)
        gw = SucceedingGateway()

        result = send_sms(self.db, reminder["id"], gateway=gw)

        self.assertIn("reminder", result)
        self.assertNotIn("status", result)  # no error key
        self.assertIsNotNone(result["reminder"]["sent_at"])

    def test_send_sms_succeeding_gateway_calls_send(self):
        """A succeeding gateway has its send() method called with the right message."""
        reminder = _sms_reminder(self.db)
        gw = SucceedingGateway()

        send_sms(self.db, reminder["id"], gateway=gw)

        self.assertEqual(len(gw.calls), 1)
        self.assertEqual(gw.calls[0]["message"], reminder["message"])

    def test_send_sms_failing_gateway_stores_reason(self):
        """A gateway that raises records the exception message as failed_reason."""
        reminder = _sms_reminder(self.db)
        gw = FailingGateway()

        result = send_sms(self.db, reminder["id"], gateway=gw)

        self.assertEqual(result["status"], 503)
        self.assertIn("network unreachable", result["reminder"]["failed_reason"])

    # -- gateway replaceability -----------------------------------------------

    def test_set_sms_gateway_replaces_global(self):
        """set_sms_gateway swaps the global instance used by send_sms."""
        gw = SucceedingGateway()
        set_sms_gateway(gw)

        reminder = _sms_reminder(self.db)
        result = send_sms(self.db, reminder["id"])

        self.assertIn("reminder", result)
        self.assertIsNotNone(result["reminder"]["sent_at"])
        self.assertEqual(len(gw.calls), 1)

    def test_get_sms_gateway_returns_null_when_not_set(self):
        """get_sms_gateway returns a NullSmsGateway when no gateway is configured."""
        set_sms_gateway(None)
        gw = get_sms_gateway()
        self.assertIsInstance(gw, NullSmsGateway)

    def test_null_sms_gateway_raises_not_configured(self):
        """NullSmsGateway.send raises SmsGatewayNotConfigured."""
        gw = NullSmsGateway()
        with self.assertRaises(SmsGatewayNotConfigured):
            gw.send("+63900000000", "hello")

    def test_duck_typed_gateway_accepted(self):
        """A plain object with send() works as a gateway (duck-typing)."""

        class DuckGateway:
            def __init__(self):
                self.sent = False

            def send(self, to, message):
                self.sent = True

        reminder = _sms_reminder(self.db)
        gw = DuckGateway()

        result = send_sms(self.db, reminder["id"], gateway=gw)

        self.assertIn("reminder", result)
        self.assertTrue(gw.sent)

    def test_sms_gateway_subclass_send_not_implemented(self):
        """SmsGateway.send raises NotImplementedError when not overridden."""
        gw = SmsGateway()
        with self.assertRaises(NotImplementedError):
            gw.send("+63900000000", "hello")


if __name__ == "__main__":
    unittest.main()
