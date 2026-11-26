"""Tests for Phase 5, task 5.4: RHU open-flags queue endpoint.

Covers:
  - GET /api/flags/queue returns only open flags (not reviewed or dismissed).
  - Every flag in the response carries the required review label.
  - The top-level response also carries the review label.
  - Results are ordered oldest-first (ascending raised_at).
  - Roles rhu_admin, midwife and physician may access the queue (200).
  - A bhw or sysadmin gets 403.
  - An unauthenticated request gets 401.

Run with: py -m unittest discover -s tests
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAFF_PASSWORD = "test-only-staff-pass"
sys.path.insert(0, str(ROOT / "server"))

from app import create_app   # noqa: E402
from db import open_database  # noqa: E402
import risk as risk_module   # noqa: E402

REVIEW_LABEL = "Requires review by a qualified health professional."


class Result:
    def __init__(self, status, content_type, body, headers):
        self.status = status
        self.data = json.loads(body) if content_type and content_type.startswith("application/json") else body


class Client:
    def __init__(self, app):
        self.app = app

    def request(self, method, path, body=None, token=None):
        headers = {}
        raw = b""
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            headers["content-type"] = "application/json"
        if token:
            headers["authorization"] = "Bearer " + token
        return Result(*self.app.handle(method, path, headers, raw))

    def sign_in(self, email, password=STAFF_PASSWORD):
        res = self.request("POST", "/api/login", body={"email": email, "password": password})
        assert res.status == 200, f"login failed for {email}: {res.data}"
        return res.data["token"]


class FlagsQueueTests(unittest.TestCase):
    """Integration tests for GET /api/flags/queue (task 5.4)."""

    @classmethod
    def setUpClass(cls):
        cls.db = open_database(":memory:", shared_password=STAFF_PASSWORD)
        cls.app = create_app(cls.db, log=lambda *a: None)
        cls.client = Client(cls.app)

        cls.tokens = {
            "rhu_admin": cls.client.sign_in("rhu.admin@rhu.example"),
            "midwife":   cls.client.sign_in("c.lim@rhu.example"),
            "physician": cls.client.sign_in("a.okafor@rhu.example"),
            "bhw":       cls.client.sign_in("b.reyes@bhw.example"),
            "sysadmin":  cls.client.sign_in("admin@example.com"),
        }

        # Retrieve the seeded BHW user id so we can insert flags directly.
        bhw_row = cls.db.execute(
            "SELECT id FROM users WHERE email = 'b.reyes@bhw.example'"
        ).fetchone()
        cls.bhw_id = bhw_row[0]

        # Retrieve (or create) a mother to attach flags to.
        mother_row = cls.db.execute("SELECT id FROM mothers LIMIT 1").fetchone()
        if mother_row:
            cls.mother_id = mother_row[0]
        else:
            # Create a minimal mother record so FK constraints are satisfied.
            cur = cls.db.execute(
                """INSERT INTO mothers (name, assigned_bhw_id, status, consent_recorded_at,
                       duplicate_key, created_by)
                   VALUES ('Test Mother Queue', ?, 'prenatal', datetime('now'),
                           'tmq-unique-dup-key', ?)""",
                (cls.bhw_id, cls.bhw_id),
            )
            cls.db.commit()
            cls.mother_id = cur.lastrowid

        # Insert flags with explicit, ordered timestamps so we can test sort order.
        cls.db.execute("DELETE FROM risk_flags WHERE mother_id = ?", (cls.mother_id,))
        cls.db.commit()

        cls.db.executemany(
            """INSERT INTO risk_flags
               (mother_id, observation, raised_by, raised_at, status, created_by,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
            [
                # Two open flags with known order
                (cls.mother_id, "High blood pressure noted", cls.bhw_id,
                 "2024-01-01T08:00:00", "open",       cls.bhw_id),
                (cls.mother_id, "Severe headache reported",  cls.bhw_id,
                 "2024-01-02T09:00:00", "open",       cls.bhw_id),
                # One reviewed flag — must NOT appear in the queue
                (cls.mother_id, "Mild oedema (reviewed)",    cls.bhw_id,
                 "2024-01-03T10:00:00", "reviewed",   cls.bhw_id),
                # One dismissed flag — must NOT appear in the queue
                (cls.mother_id, "Minor complaint (dismissed)", cls.bhw_id,
                 "2024-01-04T11:00:00", "dismissed",  cls.bhw_id),
            ],
        )
        cls.db.commit()

    # ── Core behaviour ────────────────────────────────────────────────────────

    def _get_queue(self, role):
        return self.client.request(
            "GET", "/api/flags/queue", token=self.tokens[role]
        )

    def test_queue_returns_200_for_rhu_admin(self):
        res = self._get_queue("rhu_admin")
        self.assertEqual(res.status, 200)

    def test_queue_returns_200_for_midwife(self):
        res = self._get_queue("midwife")
        self.assertEqual(res.status, 200)

    def test_queue_returns_200_for_physician(self):
        res = self._get_queue("physician")
        self.assertEqual(res.status, 200)

    def test_queue_contains_only_open_flags(self):
        """Reviewed and dismissed flags must not appear in the queue."""
        res = self._get_queue("rhu_admin")
        flags = res.data["flags"]
        statuses = {f["status"] for f in flags}
        self.assertNotIn(
            "reviewed", statuses,
            "Reviewed flags must not appear in the RHU queue.",
        )
        self.assertNotIn(
            "dismissed", statuses,
            "Dismissed flags must not appear in the RHU queue.",
        )
        for f in flags:
            self.assertEqual(f["status"], "open",
                             f"Flag id={f['id']} has unexpected status '{f['status']}'.")

    def test_queue_returns_expected_open_flag_count(self):
        res = self._get_queue("rhu_admin")
        open_flags = [f for f in res.data["flags"]
                      if f["mother_id"] == self.mother_id]
        self.assertEqual(len(open_flags), 2,
                         "Expected exactly 2 open flags for the test mother.")

    def test_every_flag_row_carries_review_label(self):
        """Each flag dict must include the exact required label string (task 5.4)."""
        res = self._get_queue("rhu_admin")
        flags = res.data["flags"]
        self.assertGreater(len(flags), 0, "Need at least one flag to test the label.")
        for flag in flags:
            self.assertIn(
                "review_label", flag,
                f"Flag id={flag.get('id')} is missing the 'review_label' field.",
            )
            self.assertEqual(
                flag["review_label"],
                REVIEW_LABEL,
                f"Flag id={flag.get('id')} has wrong label: {flag.get('review_label')!r}",
            )

    def test_top_level_review_label_is_present(self):
        """The top-level 'review_label' field must also be present in the response."""
        res = self._get_queue("rhu_admin")
        self.assertIn("review_label", res.data,
                      "Top-level 'review_label' missing from response.")
        self.assertEqual(res.data["review_label"], REVIEW_LABEL)

    def test_flags_ordered_oldest_first(self):
        """Oldest raised_at must come first (ascending order)."""
        res = self._get_queue("rhu_admin")
        our_flags = [f for f in res.data["flags"]
                     if f["mother_id"] == self.mother_id]
        raised_times = [f["raised_at"] for f in our_flags]
        self.assertEqual(
            raised_times, sorted(raised_times),
            "Open flags must be ordered by raised_at ascending (oldest first).",
        )

    # ── Access control ────────────────────────────────────────────────────────

    def test_bhw_cannot_access_queue(self):
        res = self._get_queue("bhw")
        self.assertEqual(res.status, 403,
                         "BHW must not be allowed to access the flags queue.")

    def test_sysadmin_cannot_access_queue(self):
        res = self._get_queue("sysadmin")
        self.assertEqual(res.status, 403,
                         "sysadmin must not be allowed to access the flags queue.")

    def test_unauthenticated_request_is_rejected(self):
        res = self.client.request("GET", "/api/flags/queue")
        self.assertEqual(res.status, 401,
                         "Unauthenticated requests must be rejected.")


if __name__ == "__main__":
    unittest.main()
