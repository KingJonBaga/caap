"""Tests for task 8.3: POST /api/sync — idempotent outbox upload.

Covers:
  - Sending the same client_id twice saves the record once (idempotency)
  - BHW role can POST to /api/sync (200)
  - Midwife role can POST to /api/sync (200)
  - Wrong role (mother) gets 403
  - No session gets 401
  - Processing a home_visit entity creates the visit record exactly once
  - Processing a risk_flag entity creates a flag exactly once
  - Processing a referral entity creates the referral exactly once
  - Missing outbox field returns 400
  - Item with missing client_id returns "error" status for that item
  - Item with unknown entity is accepted and stored (status "processed")
  - Multiple different client_ids in one call are all processed

Run with: py -m unittest discover -s tests
"""
import json
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAFF_PASSWORD = "test-only-staff-pass"
sys.path.insert(0, str(ROOT / "server"))

from app import create_app    # noqa: E402
from db import open_database  # noqa: E402


# ── Test helpers ──────────────────────────────────────────────────────────────

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
        res = self.request("POST", "/api/login", body={"email": email, "password": password})
        assert res.status == 200, f"login failed for {email}: {res.data}"
        return res.data["token"]


CONSENT = "2026-10-01T08:00:00Z"
FUTURE  = "2099-07-01T09:00:00"


class SyncEndpointTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db     = open_database(":memory:", shared_password=STAFF_PASSWORD)
        cls.app    = create_app(cls.db, log=lambda *a: None)
        cls.client = Client(cls.app)

        cls.tok = {
            "bhw":     cls.client.sign_in("b.reyes@bhw.example",    STAFF_PASSWORD),
            "midwife": cls.client.sign_in("c.lim@rhu.example",      STAFF_PASSWORD),
            "mother":  cls.client.sign_in("maria.santos@example.com", STAFF_PASSWORD),
        }

        # Register a mother used throughout the tests.
        res = cls.client.request(
            "POST", "/api/mothers", token=cls.tok["bhw"],
            body={"name": "Sync Test Mother", "dateOfBirth": "1992-03-15",
                  "consentRecordedAt": CONSENT},
        )
        assert res.status == 201, f"mother setup failed: {res.data}"
        cls.mid = res.data["mother"]["id"]

    def _sync(self, outbox, token=None):
        return self.client.request(
            "POST", "/api/sync",
            body={"outbox": outbox},
            token=token or self.tok["bhw"],
        )

    def _new_id(self):
        return str(uuid.uuid4())

    # ── Role checks ───────────────────────────────────────────────────────────

    def test_bhw_can_post_sync(self):
        res = self._sync([], token=self.tok["bhw"])
        self.assertEqual(res.status, 200, res.data)

    def test_midwife_can_post_sync(self):
        res = self._sync([], token=self.tok["midwife"])
        self.assertEqual(res.status, 200, res.data)

    def test_mother_role_gets_403(self):
        res = self._sync([], token=self.tok["mother"])
        self.assertEqual(res.status, 403)

    def test_no_session_gets_401(self):
        res = self.client.request("POST", "/api/sync", body={"outbox": []})
        self.assertEqual(res.status, 401)

    # ── Validation ────────────────────────────────────────────────────────────

    def test_missing_outbox_field_returns_400(self):
        res = self.client.request(
            "POST", "/api/sync",
            body={"items": []},   # wrong key
            token=self.tok["bhw"],
        )
        self.assertEqual(res.status, 400)

    def test_non_list_outbox_returns_400(self):
        res = self.client.request(
            "POST", "/api/sync",
            body={"outbox": "not-a-list"},
            token=self.tok["bhw"],
        )
        self.assertEqual(res.status, 400)

    def test_item_missing_client_id_returns_error_status(self):
        res = self._sync([{"entity": "home_visit", "payload": {}}])
        self.assertEqual(res.status, 200)
        result = res.data["results"][0]
        self.assertEqual(result["status"], "error")

    # ── Idempotency ───────────────────────────────────────────────────────────

    def test_same_client_id_sent_twice_is_saved_once(self):
        """Core idempotency requirement from task 8.3."""
        from common import query_all

        client_id = self._new_id()
        payload = {"motherId": self.mid, "scheduledFor": FUTURE, "notes": "idempotency check"}

        # First upload — should succeed.
        res1 = self._sync([{"client_id": client_id, "entity": "home_visit", "payload": payload}])
        self.assertEqual(res1.status, 200, res1.data)
        self.assertEqual(res1.data["results"][0]["status"], "processed")

        # Second upload with identical client_id — should be a duplicate.
        res2 = self._sync([{"client_id": client_id, "entity": "home_visit", "payload": payload}])
        self.assertEqual(res2.status, 200, res2.data)
        self.assertEqual(res2.data["results"][0]["status"], "duplicate")

        # sync_outbox must contain exactly one row for this client_id.
        rows = query_all(
            self.db,
            "SELECT * FROM sync_outbox WHERE client_id = ?",
            (client_id,),
        )
        self.assertEqual(len(rows), 1, "sync_outbox should have exactly one row for the client_id")

    def test_idempotency_across_separate_calls(self):
        """Sending the same outbox in two separate HTTP calls saves the record once."""
        client_id = self._new_id()
        payload = {"motherId": self.mid, "scheduledFor": FUTURE}

        self._sync([{"client_id": client_id, "entity": "home_visit", "payload": payload}])
        res = self._sync([{"client_id": client_id, "entity": "home_visit", "payload": payload}])
        self.assertEqual(res.data["results"][0]["status"], "duplicate")

    # ── Entity dispatch: home_visit ───────────────────────────────────────────

    def test_home_visit_entity_creates_visit_record(self):
        """A home_visit item creates exactly one row in home_visits."""
        from common import query_all

        client_id = self._new_id()
        notes_text = f"visit-{client_id[:8]}"
        payload = {"motherId": self.mid, "scheduledFor": FUTURE, "notes": notes_text}

        res = self._sync([{"client_id": client_id, "entity": "home_visit", "payload": payload}])
        self.assertEqual(res.status, 200, res.data)
        self.assertEqual(res.data["results"][0]["status"], "processed")

        rows = query_all(
            self.db,
            "SELECT * FROM home_visits WHERE notes = ?",
            (notes_text,),
        )
        self.assertEqual(len(rows), 1, "Expected exactly one home_visit row in the database")

    def test_home_visit_entity_idempotent_creates_visit_only_once(self):
        """Sending the same home_visit twice creates only one visit row."""
        from common import query_all

        client_id = self._new_id()
        notes_text = f"dedup-{client_id[:8]}"
        payload = {"motherId": self.mid, "scheduledFor": FUTURE, "notes": notes_text}
        item = {"client_id": client_id, "entity": "home_visit", "payload": payload}

        self._sync([item])
        self._sync([item])

        rows = query_all(self.db, "SELECT * FROM home_visits WHERE notes = ?", (notes_text,))
        self.assertEqual(len(rows), 1, "Idempotent upload should not create duplicate visit rows")

    def test_home_visit_missing_mother_id_returns_error(self):
        client_id = self._new_id()
        payload = {"scheduledFor": FUTURE}  # no motherId
        res = self._sync([{"client_id": client_id, "entity": "home_visit", "payload": payload}])
        self.assertEqual(res.status, 200)
        self.assertEqual(res.data["results"][0]["status"], "error")

    # ── Entity dispatch: risk_flag ────────────────────────────────────────────

    def test_risk_flag_entity_creates_flag(self):
        from common import query_all

        client_id = self._new_id()
        obs_text = f"obs-{client_id[:8]}"
        payload = {"motherId": self.mid, "observation": obs_text}

        res = self._sync([{"client_id": client_id, "entity": "risk_flag", "payload": payload}])
        self.assertEqual(res.status, 200, res.data)
        self.assertEqual(res.data["results"][0]["status"], "processed")

        rows = query_all(
            self.db, "SELECT * FROM risk_flags WHERE observation = ?", (obs_text,)
        )
        self.assertEqual(len(rows), 1, "Expected one risk_flag row")

    def test_two_different_risk_flags_for_same_mother_are_both_kept(self):
        """Conflict rule: clinical flags are never silently overwritten (design.md §5)."""
        from common import query_all

        obs1 = f"flag-a-{self._new_id()[:8]}"
        obs2 = f"flag-b-{self._new_id()[:8]}"

        self._sync([
            {"client_id": self._new_id(), "entity": "risk_flag",
             "payload": {"motherId": self.mid, "observation": obs1}},
            {"client_id": self._new_id(), "entity": "risk_flag",
             "payload": {"motherId": self.mid, "observation": obs2}},
        ])

        rows = query_all(
            self.db,
            "SELECT * FROM risk_flags WHERE observation IN (?, ?)",
            (obs1, obs2),
        )
        self.assertEqual(len(rows), 2, "Both flags must be kept (conflict rule)")

    # ── Entity dispatch: referral ─────────────────────────────────────────────

    def test_referral_entity_creates_referral(self):
        from common import query_all

        client_id = self._new_id()
        reason = f"reason-{client_id[:8]}"
        payload = {
            "motherId": self.mid,
            "reason":   reason,
            "toFacilityOrUser": "RHU-001",
        }

        res = self._sync([{"client_id": client_id, "entity": "referral", "payload": payload}])
        self.assertEqual(res.status, 200, res.data)
        self.assertEqual(res.data["results"][0]["status"], "processed")

        rows = query_all(self.db, "SELECT * FROM referrals WHERE reason = ?", (reason,))
        self.assertEqual(len(rows), 1, "Expected one referral row")

    # ── Unknown entity ────────────────────────────────────────────────────────

    def test_unknown_entity_is_accepted_and_stored(self):
        """An unrecognised entity is stored in sync_outbox but no secondary write fails."""
        from common import query_all

        client_id = self._new_id()
        res = self._sync([{"client_id": client_id, "entity": "future_entity",
                           "payload": {"data": "x"}}])
        self.assertEqual(res.status, 200, res.data)
        result = res.data["results"][0]
        self.assertEqual(result["status"], "processed")
        self.assertIn("note", result)

        rows = query_all(
            self.db, "SELECT * FROM sync_outbox WHERE client_id = ?", (client_id,)
        )
        self.assertEqual(len(rows), 1, "Unknown entity should still be recorded in sync_outbox")

    # ── Mixed batch ───────────────────────────────────────────────────────────

    def test_mixed_batch_all_processed(self):
        """A single POST with multiple different items all get processed."""
        obs_text = f"batch-obs-{self._new_id()[:8]}"
        items = [
            {"client_id": self._new_id(), "entity": "home_visit",
             "payload": {"motherId": self.mid, "scheduledFor": FUTURE}},
            {"client_id": self._new_id(), "entity": "risk_flag",
             "payload": {"motherId": self.mid, "observation": obs_text}},
        ]
        res = self._sync(items)
        self.assertEqual(res.status, 200, res.data)
        results = res.data["results"]
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertEqual(r["status"], "processed", r)

    def test_batch_with_duplicate_in_middle(self):
        """When a batch contains a duplicate, only that item is skipped."""
        client_id = self._new_id()
        payload = {"motherId": self.mid, "scheduledFor": FUTURE}

        # First upload — registers the client_id.
        self._sync([{"client_id": client_id, "entity": "home_visit", "payload": payload}])

        # Second batch: new item, duplicate, new item.
        new1 = self._new_id()
        new2 = self._new_id()
        obs  = f"mid-dup-{new1[:8]}"
        res  = self._sync([
            {"client_id": new1,       "entity": "home_visit",
             "payload": {"motherId": self.mid, "scheduledFor": FUTURE}},
            {"client_id": client_id,  "entity": "home_visit", "payload": payload},
            {"client_id": new2,       "entity": "risk_flag",
             "payload": {"motherId": self.mid, "observation": obs}},
        ])
        self.assertEqual(res.status, 200, res.data)
        statuses = [r["status"] for r in res.data["results"]]
        self.assertEqual(statuses, ["processed", "duplicate", "processed"])

    # ── Audit log ─────────────────────────────────────────────────────────────

    def test_audit_row_written_on_sync(self):
        from common import query_all

        self._sync([])  # empty outbox still triggers audit
        rows = query_all(
            self.db,
            "SELECT * FROM audit_log WHERE action = 'sync_uploaded' ORDER BY id DESC LIMIT 1",
        )
        self.assertTrue(len(rows) >= 1, "Expected an audit_log row for sync_uploaded")


if __name__ == "__main__":
    unittest.main()
