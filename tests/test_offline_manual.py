"""Task 8.7 — Offline phone test (automated portion).

Design context
--------------
Task 8.7 is documented as a manual test (see docs/offline-phone-test.md).  The manual
steps — opening Chrome DevTools, setting a 390×844 viewport, toggling the Network "Offline"
checkbox — cannot be automated in a standard Python unittest environment.

What *can* be verified automatically is the server-side sequence that the manual test
exercises:

  1. A BHW device queues changes while offline (simulated here by directly building outbox
     items — the front-end normally writes them to IndexedDB first).
  2. When connectivity returns the client POSTs those items to POST /api/sync.
  3. The server processes each item exactly once (idempotency via sync_outbox.client_id).
  4. The server records appear in the database (home visit, risk flag, referral).
  5. A second POST with the same client_ids returns "duplicate" for every item.
  6. sync_outbox contains exactly one row per client_id.

Run with:  py -m unittest discover -s tests
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


# ── Minimal test client (same pattern as test_sync.py) ───────────────────────

class _Result:
    def __init__(self, status, content_type, body, headers):
        self.status = status
        self.data = json.loads(body) if content_type and "json" in content_type else body


class _Client:
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
        return _Result(*self.app.handle(method, path, headers, raw))

    def sign_in(self, email, password):
        res = self.request("POST", "/api/login", body={"email": email, "password": password})
        assert res.status == 200, f"login failed for {email}: {res.data}"
        return res.data["token"]


CONSENT = "2026-10-01T08:00:00Z"
FUTURE  = "2099-12-01T09:00:00"


# ── Manual-test placeholder ───────────────────────────────────────────────────

class OfflinePhoneManualTestDocumentation(unittest.TestCase):
    """Placeholder class so the test runner finds no errors for the manual portion.

    The actual steps are documented in docs/offline-phone-test.md.  Run that
    checklist manually using Chrome DevTools to verify phone-sized layout,
    service-worker registration, offline queueing, reconnect sync, and
    post-sign-out IndexedDB clearing.
    """

    def test_manual_test_script_exists(self):
        """The manual test script document must be present in the repo."""
        script = ROOT / "docs" / "offline-phone-test.md"
        self.assertTrue(
            script.exists(),
            f"Manual test script not found at {script}. "
            "Create docs/offline-phone-test.md before running field tests.",
        )
        content = script.read_text(encoding="utf-8")
        # Spot-check for key sections.
        self.assertIn("Service Worker", content, "Script should reference service-worker setup")
        self.assertIn("Offline", content, "Script should cover going offline")
        self.assertIn("Sync now", content, "Script should cover the Sync now button")
        self.assertIn("IndexedDB", content, "Script should cover IndexedDB verification on sign-out")


# ── Automated: offline → reconnect sequence ───────────────────────────────────

class OfflineReconnectSequenceTests(unittest.TestCase):
    """Automated tests that mirror the server-side behaviour validated by the
    manual phone test (docs/offline-phone-test.md).

    Scenario:
      A BHW device loses connectivity and the front-end queues three changes in
      IndexedDB (home visit, risk flag, referral).  When connectivity returns the
      client uploads the outbox via POST /api/sync.  This test verifies that:

      - All three items are accepted and appear in the database (online-reconnect path).
      - Repeating the POST with the same client_ids produces "duplicate" for each item.
      - sync_outbox has exactly one row per client_id (idempotency guarantee from design §5).
    """

    @classmethod
    def setUpClass(cls):
        cls.db     = open_database(":memory:", shared_password=STAFF_PASSWORD)
        cls.app    = create_app(cls.db, log=lambda *a: None)
        cls.client = _Client(cls.app)

        cls.bhw_token = cls.client.sign_in("b.reyes@bhw.example", STAFF_PASSWORD)

        # Register a mother to attach visits/flags/referrals to.
        res = cls.client.request(
            "POST", "/api/mothers",
            token=cls.bhw_token,
            body={
                "name": "Offline Reconnect Mother",
                "dateOfBirth": "1993-05-20",
                "consentRecordedAt": CONSENT,
            },
        )
        assert res.status == 201, f"mother setup failed: {res.data}"
        cls.mother_id = res.data["mother"]["id"]

    # ── helpers ───────────────────────────────────────────────────────────────

    def _new_id(self):
        return str(uuid.uuid4())

    def _post_sync(self, outbox):
        return self.client.request(
            "POST", "/api/sync",
            body={"outbox": outbox},
            token=self.bhw_token,
        )

    def _query_all(self, sql, params=()):
        from common import query_all
        return query_all(self.db, sql, params)

    # ── tests ─────────────────────────────────────────────────────────────────

    def test_offline_queue_then_reconnect_all_three_entities_accepted(self):
        """Simulate: BHW queues home visit + risk flag + referral offline, then
        POSTs them on reconnect.  All three should be processed and appear on
        the server (design.md §5)."""

        hv_id  = self._new_id()
        rf_id  = self._new_id()
        ref_id = self._new_id()

        obs_text    = f"reconnect-obs-{hv_id[:8]}"
        reason_text = f"reconnect-reason-{ref_id[:8]}"

        # This is the outbox the client accumulates offline.
        offline_outbox = [
            {
                "client_id": hv_id,
                "entity":    "home_visit",
                "payload":   {
                    "motherId":     self.mother_id,
                    "scheduledFor": FUTURE,
                    "notes":        f"visit-{hv_id[:8]}",
                },
            },
            {
                "client_id": rf_id,
                "entity":    "risk_flag",
                "payload":   {
                    "motherId":    self.mother_id,
                    "observation": obs_text,
                },
            },
            {
                "client_id": ref_id,
                "entity":    "referral",
                "payload":   {
                    "motherId":          self.mother_id,
                    "reason":            reason_text,
                    "toFacilityOrUser":  "RHU-001",
                },
            },
        ]

        # ── First upload (reconnect) ──────────────────────────────────────────
        res = self._post_sync(offline_outbox)
        self.assertEqual(res.status, 200, f"sync failed: {res.data}")

        statuses = [r["status"] for r in res.data["results"]]
        self.assertEqual(
            statuses, ["processed", "processed", "processed"],
            f"Expected all three items processed; got {statuses}",
        )

        # Verify the home visit record was created.
        hv_rows = self._query_all(
            "SELECT * FROM home_visits WHERE notes = ?",
            (f"visit-{hv_id[:8]}",),
        )
        self.assertEqual(len(hv_rows), 1, "Home visit should exist in home_visits table")

        # Verify the risk flag was created.
        flag_rows = self._query_all(
            "SELECT * FROM risk_flags WHERE observation = ?",
            (obs_text,),
        )
        self.assertEqual(len(flag_rows), 1, "Risk flag should exist in risk_flags table")

        # Verify the referral was created.
        ref_rows = self._query_all(
            "SELECT * FROM referrals WHERE reason = ?",
            (reason_text,),
        )
        self.assertEqual(len(ref_rows), 1, "Referral should exist in referrals table")

    def test_second_upload_with_same_client_ids_returns_all_duplicates(self):
        """Reconnect then upload the same outbox again — every item must be a
        duplicate (idempotency: design.md §5, task 8.3)."""

        cid1 = self._new_id()
        cid2 = self._new_id()

        outbox = [
            {
                "client_id": cid1,
                "entity":    "home_visit",
                "payload":   {"motherId": self.mother_id, "scheduledFor": FUTURE},
            },
            {
                "client_id": cid2,
                "entity":    "risk_flag",
                "payload":   {"motherId": self.mother_id, "observation": f"dup-obs-{cid2[:8]}"},
            },
        ]

        # First upload.
        r1 = self._post_sync(outbox)
        self.assertEqual(r1.status, 200)
        self.assertTrue(
            all(r["status"] == "processed" for r in r1.data["results"]),
            f"First upload: expected processed, got {[r['status'] for r in r1.data['results']]}",
        )

        # Second upload (same outbox — simulates a network hiccup during first upload).
        r2 = self._post_sync(outbox)
        self.assertEqual(r2.status, 200)
        self.assertTrue(
            all(r["status"] == "duplicate" for r in r2.data["results"]),
            f"Second upload: expected all duplicates, got {[r['status'] for r in r2.data['results']]}",
        )

    def test_sync_outbox_has_exactly_one_row_per_client_id(self):
        """sync_outbox.client_id deduplication: each client_id appears exactly
        once regardless of how many times the same outbox is uploaded."""

        cid = self._new_id()
        item = [{
            "client_id": cid,
            "entity":    "home_visit",
            "payload":   {"motherId": self.mother_id, "scheduledFor": FUTURE},
        }]

        self._post_sync(item)
        self._post_sync(item)  # second upload — must not create a second row
        self._post_sync(item)  # third  upload — must not create a third  row

        rows = self._query_all(
            "SELECT * FROM sync_outbox WHERE client_id = ?",
            (cid,),
        )
        self.assertEqual(
            len(rows), 1,
            f"sync_outbox should contain exactly one row for client_id {cid}; found {len(rows)}",
        )

    def test_audit_log_records_sync_upload(self):
        """Every sync upload (even an empty one) should write an audit row
        (design.md §9)."""
        from common import query_all

        # Empty outbox — still triggers an audit entry.
        self._post_sync([])

        rows = query_all(
            self.db,
            "SELECT * FROM audit_log WHERE action = 'sync_uploaded' ORDER BY id DESC LIMIT 1",
        )
        self.assertTrue(len(rows) >= 1, "Expected audit_log row for sync_uploaded")


if __name__ == "__main__":
    unittest.main()
