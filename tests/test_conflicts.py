"""Tests for task 8.4: Conflict rule — clinical flags are never overwritten.

Design.md §5:
  "Conflict rule: the server keeps the latest change per field, and every
  rejected or overwritten value is written to audit_log with both values.
  Clinical flags are never silently overwritten: two flags for the same visit
  are both kept."

Covers:
  - Two different risk flags for the same mother are both kept.
  - Two risk flags tied to the same visit_id are both kept as separate rows.
  - record_observation never updates an existing flag row — it always inserts.
  - Each new flag gets status='open' regardless of earlier flags on the same mother.
  - Submitting the same observation text twice for the same mother creates two rows
    (append-only, no de-duplication by text).
  - The API endpoint POST /api/mothers/:id/observations creates independent flags
    when called twice.

Run with: py -m unittest discover -s tests
"""

import sys
import unittest
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAFF_PASSWORD = "test-only-staff-pass"
sys.path.insert(0, str(ROOT / "server"))

from app import create_app    # noqa: E402
from db import open_database  # noqa: E402
import risk as risk_module    # noqa: E402
from common import query_all  # noqa: E402


# ── Minimal HTTP test client (mirrors test_sync.py) ───────────────────────────

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
        res = self.request(
            "POST", "/api/login",
            body={"email": email, "password": password},
        )
        assert res.status == 200, f"login failed for {email}: {res.data}"
        return res.data["token"]


CONSENT = "2026-10-01T08:00:00Z"
FUTURE  = "2099-07-01T09:00:00"


class FlagConflictRuleTests(unittest.TestCase):
    """Core test class for task 8.4.

    All tests assert that clinical flags are append-only: no INSERT ever
    overwrites or replaces an existing flag row for the same mother or visit.
    """

    @classmethod
    def setUpClass(cls):
        cls.db     = open_database(":memory:", shared_password=STAFF_PASSWORD)
        cls.app    = create_app(cls.db, log=lambda *a: None)
        cls.client = Client(cls.app)

        cls.tok = {
            "bhw":     cls.client.sign_in("b.reyes@bhw.example",    STAFF_PASSWORD),
            "midwife": cls.client.sign_in("c.lim@rhu.example",      STAFF_PASSWORD),
        }

        # Register a mother used throughout the tests.
        res = cls.client.request(
            "POST", "/api/mothers", token=cls.tok["bhw"],
            body={
                "name": "Conflict Test Mother",
                "dateOfBirth": "1990-05-20",
                "consentRecordedAt": CONSENT,
            },
        )
        assert res.status == 201, f"mother setup failed: {res.data}"
        cls.mid = res.data["mother"]["id"]

        # Create a home visit so we have a real visit_id to reference.
        res2 = cls.client.request(
            "POST", "/api/mothers/{}/home-visits".format(cls.mid),
            token=cls.tok["bhw"],
            body={"scheduledFor": FUTURE, "notes": "conflict-test visit"},
        )
        assert res2.status == 201, f"home visit setup failed: {res2.data}"
        cls.hv_id = res2.data["visit"]["id"]

        # Resolve a bhw user dict for direct module calls.
        from common import query_one
        cls.bhw_user = query_one(
            cls.db,
            "SELECT * FROM users WHERE email = 'b.reyes@bhw.example'",
        )

    # ── Core conflict rule: flags are always appended ─────────────────────────

    def test_two_flags_same_mother_both_kept(self):
        """Two different observations for the same mother produce two distinct rows."""
        obs_a = "elevated blood pressure reading"
        obs_b = "swelling in lower limbs"

        result_a = risk_module.record_observation(
            self.db, self.bhw_user, self.mid, {"observation": obs_a}
        )
        result_b = risk_module.record_observation(
            self.db, self.bhw_user, self.mid, {"observation": obs_b}
        )

        self.assertIn("flag", result_a, f"First observation failed: {result_a}")
        self.assertIn("flag", result_b, f"Second observation failed: {result_b}")

        # Must be different rows.
        self.assertNotEqual(
            result_a["flag"]["id"], result_b["flag"]["id"],
            "Each flag must be a separate row.",
        )

        rows = query_all(
            self.db,
            "SELECT * FROM risk_flags WHERE mother_id = ? AND observation IN (?, ?)",
            (self.mid, obs_a, obs_b),
        )
        self.assertEqual(len(rows), 2, "Both flags must be persisted.")

    def test_two_flags_same_visit_id_both_kept(self):
        """Two observations tied to the same visit_id both produce distinct rows.

        This is the direct requirement from task 8.4:
        'two different flags for the same visit are both kept.'
        """
        obs_x = "headache reported during home visit"
        obs_y = "nausea reported during home visit"

        result_x = risk_module.record_observation(
            self.db, self.bhw_user, self.mid,
            {"observation": obs_x, "visitId": self.hv_id},
        )
        result_y = risk_module.record_observation(
            self.db, self.bhw_user, self.mid,
            {"observation": obs_y, "visitId": self.hv_id},
        )

        self.assertIn("flag", result_x, f"First visit flag failed: {result_x}")
        self.assertIn("flag", result_y, f"Second visit flag failed: {result_y}")
        self.assertNotEqual(
            result_x["flag"]["id"], result_y["flag"]["id"],
            "Each flag for the same visit must be a separate row.",
        )

        rows = query_all(
            self.db,
            "SELECT * FROM risk_flags WHERE visit_id = ? AND mother_id = ?",
            (self.hv_id, self.mid),
        )
        self.assertGreaterEqual(
            len(rows), 2,
            "Both flags associated with the same visit must be kept.",
        )

    def test_flag_never_overwrites_existing_flag(self):
        """Adding a new flag must not change the row count of earlier flags.

        Each call to record_observation must increase the total flag count by 1.
        """
        before = query_all(
            self.db,
            "SELECT COUNT(*) AS n FROM risk_flags WHERE mother_id = ?",
            (self.mid,),
        )[0]["n"]

        risk_module.record_observation(
            self.db, self.bhw_user, self.mid,
            {"observation": "new distinct observation for overwrite test"},
        )

        after = query_all(
            self.db,
            "SELECT COUNT(*) AS n FROM risk_flags WHERE mother_id = ?",
            (self.mid,),
        )[0]["n"]

        self.assertEqual(
            after, before + 1,
            "record_observation must INSERT a new row, never UPDATE an existing one.",
        )

    def test_identical_observation_text_twice_creates_two_rows(self):
        """Submitting the exact same observation text twice still creates two rows.

        Flags are never de-duplicated by content — only sync_outbox.client_id
        provides idempotency for the offline sync path.
        """
        obs = "repeated-observation-text-for-conflict-test"

        risk_module.record_observation(
            self.db, self.bhw_user, self.mid, {"observation": obs}
        )
        risk_module.record_observation(
            self.db, self.bhw_user, self.mid, {"observation": obs}
        )

        rows = query_all(
            self.db,
            "SELECT * FROM risk_flags WHERE mother_id = ? AND observation = ?",
            (self.mid, obs),
        )
        self.assertEqual(
            len(rows), 2,
            "Identical observation text must not suppress a second flag insert.",
        )

    def test_new_flag_always_starts_open(self):
        """Every newly recorded flag must have status='open' regardless of earlier flags."""
        for obs in ("flag-status-check-obs-1", "flag-status-check-obs-2"):
            result = risk_module.record_observation(
                self.db, self.bhw_user, self.mid, {"observation": obs}
            )
            self.assertIn("flag", result)
            self.assertEqual(
                result["flag"]["status"], "open",
                f"New flag must start with status='open', got {result['flag']['status']}.",
            )

    # ── visit_id field presence ───────────────────────────────────────────────

    def test_flag_stores_visit_id_when_provided(self):
        """A flag created with visitId must have that visit_id stored."""
        obs = "visit-id-storage-test-observation"
        result = risk_module.record_observation(
            self.db, self.bhw_user, self.mid,
            {"observation": obs, "visitId": self.hv_id},
        )
        self.assertIn("flag", result)
        self.assertEqual(
            result["flag"]["visit_id"], self.hv_id,
            "visit_id must be persisted from the payload.",
        )

    def test_flag_visit_id_is_null_when_not_provided(self):
        """A flag created without a visitId has visit_id=None."""
        obs = "no-visit-id-observation-test"
        result = risk_module.record_observation(
            self.db, self.bhw_user, self.mid, {"observation": obs}
        )
        self.assertIn("flag", result)
        self.assertIsNone(
            result["flag"]["visit_id"],
            "visit_id should be None when not provided.",
        )

    # ── API layer: POST /api/mothers/:id/observations ─────────────────────────

    def test_api_two_observations_both_create_flags(self):
        """Calling the API endpoint twice creates two independent flags."""
        def post_obs(text, token=None):
            return self.client.request(
                "POST",
                "/api/mothers/{}/observations".format(self.mid),
                body={"observation": text},
                token=token or self.tok["bhw"],
            )

        obs_1 = "api-conflict-test-obs-alpha"
        obs_2 = "api-conflict-test-obs-beta"

        res1 = post_obs(obs_1)
        res2 = post_obs(obs_2)

        self.assertEqual(res1.status, 201, res1.data)
        self.assertEqual(res2.status, 201, res2.data)
        self.assertNotEqual(
            res1.data["flag"]["id"], res2.data["flag"]["id"],
            "Each API call must create a separate flag row.",
        )

    def test_api_two_visit_flags_both_persisted(self):
        """POST observations with same visitId via API — both flags kept."""
        def post_obs(text, visit_id=None):
            body = {"observation": text}
            if visit_id is not None:
                body["visitId"] = visit_id
            return self.client.request(
                "POST",
                "/api/mothers/{}/observations".format(self.mid),
                body=body,
                token=self.tok["bhw"],
            )

        res_a = post_obs("api-visit-flag-obs-1", self.hv_id)
        res_b = post_obs("api-visit-flag-obs-2", self.hv_id)

        self.assertEqual(res_a.status, 201, res_a.data)
        self.assertEqual(res_b.status, 201, res_b.data)

        # Both rows should exist in the database.
        rows = query_all(
            self.db,
            "SELECT * FROM risk_flags WHERE visit_id = ? AND observation IN (?, ?)",
            (self.hv_id, "api-visit-flag-obs-1", "api-visit-flag-obs-2"),
        )
        self.assertEqual(
            len(rows), 2,
            "Both API-submitted flags for the same visit must be kept.",
        )

    # ── Sync path: risk_flag entity via /api/sync ─────────────────────────────

    def test_sync_risk_flag_entity_creates_separate_rows(self):
        """Two distinct client_ids with risk_flag entity both produce a row in risk_flags."""
        import uuid

        def sync(items):
            return self.client.request(
                "POST", "/api/sync",
                body={"outbox": items},
                token=self.tok["bhw"],
            )

        obs_p = f"sync-conflict-obs-p-{self.mid}"
        obs_q = f"sync-conflict-obs-q-{self.mid}"

        res = sync([
            {
                "client_id": str(uuid.uuid4()),
                "entity": "risk_flag",
                "payload": {"motherId": self.mid, "observation": obs_p},
            },
            {
                "client_id": str(uuid.uuid4()),
                "entity": "risk_flag",
                "payload": {"motherId": self.mid, "observation": obs_q},
            },
        ])

        self.assertEqual(res.status, 200, res.data)
        for r in res.data["results"]:
            self.assertEqual(r["status"], "processed", r)

        rows = query_all(
            self.db,
            "SELECT * FROM risk_flags WHERE observation IN (?, ?)",
            (obs_p, obs_q),
        )
        self.assertEqual(
            len(rows), 2,
            "Both risk_flag sync items must produce separate risk_flags rows.",
        )


if __name__ == "__main__":
    unittest.main()
