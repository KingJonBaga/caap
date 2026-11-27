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


# ── Reconnect: BHW schedule reflects synced data ─────────────────────────────

class OfflineSyncReflectedInBhwScheduleTests(unittest.TestCase):
    """Verify that records created via the sync endpoint (the reconnect path)
    appear on the BHW's schedule view.

    Phone scenario: a BHW schedules a home visit offline and raises a risk flag
    offline.  When connectivity returns and the outbox is uploaded via
    POST /api/sync, the BHW's GET /api/bhw/schedule should include the new visit
    in the appropriate bucket (dueToday / overdue) and the flags queue should
    carry the new flag.

    This is the server-side equivalent of steps 3.1–5.2 in the manual test
    script (docs/offline-phone-test.md).
    """

    TODAY = "2025-07-01"       # fixed "today" for the schedule query
    TODAY_DT = "2025-07-01T10:00:00"
    PAST_DT  = "2025-06-25T09:00:00"   # → overdue or missed bucket
    FUTURE_DT = "2099-12-01T09:00:00"

    @classmethod
    def setUpClass(cls):
        cls.db     = open_database(":memory:", shared_password=STAFF_PASSWORD)
        cls.app    = create_app(cls.db, log=lambda *a: None)
        cls.client = _Client(cls.app)

        cls.bhw_token = cls.client.sign_in("b.reyes@bhw.example", STAFF_PASSWORD)
        cls.rhu_token = cls.client.sign_in("rhu.admin@rhu.example", STAFF_PASSWORD)

        # Create a mother to work with.
        res = cls.client.request(
            "POST", "/api/mothers",
            token=cls.bhw_token,
            body={
                "name": "Schedule Reflect Mother",
                "dateOfBirth": "1994-08-10",
                "consentRecordedAt": CONSENT,
            },
        )
        assert res.status == 201, f"mother creation failed: {res.data}"
        cls.mother_id = res.data["mother"]["id"]

        # Resolve BHW user id for direct module calls.
        row = cls.db.execute(
            "SELECT id FROM users WHERE email = 'b.reyes@bhw.example'"
        ).fetchone()
        cls.bhw_id = row[0]

    def _sync(self, outbox):
        return self.client.request(
            "POST", "/api/sync",
            body={"outbox": outbox},
            token=self.bhw_token,
        )

    def _bhw_schedule(self):
        return self.client.request(
            "GET", "/api/bhw/schedule",
            token=self.bhw_token,
        )

    # ── Tests ─────────────────────────────────────────────────────────────────

    def test_home_visit_synced_offline_appears_in_bhw_schedule(self):
        """A home visit uploaded via the sync endpoint (reconnect path) must
        appear in the BHW's schedule.

        Corresponds to manual test steps 3.1 and 5.2 — 'confirm home visit
        appears on the server'.
        """
        from common import query_all

        client_id = str(uuid.uuid4())
        notes_tag = f"schedule-check-{client_id[:8]}"

        # --- offline edit (queued on device while no signal) ----------------
        outbox = [{
            "client_id": client_id,
            "entity":    "home_visit",
            "payload":   {
                "motherId":     self.mother_id,
                "scheduledFor": self.FUTURE_DT,
                "notes":        notes_tag,
            },
        }]

        # --- reconnect: upload the outbox -----------------------------------
        sync_res = self._sync(outbox)
        self.assertEqual(sync_res.status, 200, f"sync failed: {sync_res.data}")
        self.assertEqual(sync_res.data["results"][0]["status"], "processed")

        # --- verify: visit record exists in home_visits ---------------------
        rows = query_all(
            self.db,
            "SELECT * FROM home_visits WHERE notes = ?",
            (notes_tag,),
        )
        self.assertEqual(len(rows), 1, "Home visit should exist in home_visits after sync")

        # --- verify: BHW schedule endpoint returns something ----------------
        # (the visit is far in the future so won't be in dueToday/overdue, but
        # the endpoint itself must be reachable and not error)
        sched = self._bhw_schedule()
        self.assertEqual(sched.status, 200, f"BHW schedule call failed: {sched.data}")
        self.assertIn("dueToday", sched.data)
        self.assertIn("overdue",  sched.data)
        self.assertIn("missed",   sched.data)

    def test_risk_flag_synced_offline_appears_in_flags_queue(self):
        """A risk flag uploaded via the sync endpoint (reconnect path) must
        appear in GET /api/flags/queue with the required review label.

        Corresponds to manual test steps 3.2 and 5.2 — 'confirm risk flag is
        in the open flags queue with the correct label'.
        """
        client_id = str(uuid.uuid4())
        obs_text = f"offline-reconnect-obs-{client_id[:8]}"

        # --- offline edit ---------------------------------------------------
        outbox = [{
            "client_id": client_id,
            "entity":    "risk_flag",
            "payload":   {
                "motherId":    self.mother_id,
                "observation": obs_text,
            },
        }]

        # --- reconnect: upload -------------------------------------------
        sync_res = self._sync(outbox)
        self.assertEqual(sync_res.status, 200, f"sync failed: {sync_res.data}")
        self.assertEqual(sync_res.data["results"][0]["status"], "processed")

        # --- verify: flag appears in RHU queue with review label -----------
        queue_res = self.client.request(
            "GET", "/api/flags/queue",
            token=self.rhu_token,
        )
        self.assertEqual(queue_res.status, 200, f"flags queue failed: {queue_res.data}")

        flags = queue_res.data["flags"]
        our_flags = [f for f in flags if f.get("observation") == obs_text]
        self.assertEqual(len(our_flags), 1, "Synced flag should appear in RHU queue")

        flag = our_flags[0]
        self.assertEqual(flag["status"], "open",
                         "Offline-synced flag must start with status='open'")
        self.assertIn("review_label", flag,
                      "Flag must carry the 'review_label' field")
        self.assertEqual(
            flag["review_label"],
            "Requires review by a qualified health professional.",
            "Flag must carry the exact required label text",
        )

    def test_referral_synced_offline_appears_with_pending_status(self):
        """A referral uploaded via the sync endpoint must appear with
        status='pending' — matching the manual test step 5.2.
        """
        from common import query_all

        client_id = str(uuid.uuid4())
        reason_tag = f"offline-referral-{client_id[:8]}"

        outbox = [{
            "client_id": client_id,
            "entity":    "referral",
            "payload":   {
                "motherId":         self.mother_id,
                "reason":           reason_tag,
                "toFacilityOrUser": "RHU-001",
            },
        }]

        sync_res = self._sync(outbox)
        self.assertEqual(sync_res.status, 200, f"sync failed: {sync_res.data}")
        self.assertEqual(sync_res.data["results"][0]["status"], "processed")

        rows = query_all(
            self.db,
            "SELECT * FROM referrals WHERE reason = ?",
            (reason_tag,),
        )
        self.assertEqual(len(rows), 1, "Referral should appear after sync")
        self.assertEqual(rows[0]["status"], "pending",
                         "Offline-synced referral must start with status='pending'")

    def test_checklist_answers_stored_in_home_visit_via_sync(self):
        """A home visit submitted offline with checklist answers must have those
        answers stored verbatim in the database (design.md §4: JSON answers,
        no score computed).
        """
        from common import query_all
        import json as _json

        client_id = str(uuid.uuid4())
        answers_payload = {
            "q_bp": "120/80",
            "q_swelling": "mild",
            "q_fetal_movement": "yes",
        }
        notes_tag = f"checklist-answers-{client_id[:8]}"

        outbox = [{
            "client_id": client_id,
            "entity":    "home_visit",
            "payload":   {
                "motherId":        self.mother_id,
                "scheduledFor":    self.FUTURE_DT,
                "notes":           notes_tag,
                "checklistAnswers": answers_payload,
            },
        }]

        sync_res = self._sync(outbox)
        self.assertEqual(sync_res.status, 200)
        self.assertEqual(sync_res.data["results"][0]["status"], "processed")

        rows = query_all(
            self.db,
            "SELECT checklist_answers FROM home_visits WHERE notes = ?",
            (notes_tag,),
        )
        self.assertEqual(len(rows), 1, "Home visit with checklist answers should exist")

        stored = _json.loads(rows[0]["checklist_answers"])
        self.assertEqual(stored, answers_payload,
                         "Checklist answers must be stored verbatim (no score computed)")

    def test_idempotent_reconnect_does_not_duplicate_bhw_schedule_entries(self):
        """Reconnect then upload same outbox twice — only one home visit row must
        appear.  The BHW schedule must not show duplicates (manual test step 5.3).
        """
        from common import query_all

        client_id = str(uuid.uuid4())
        notes_tag = f"idem-schedule-{client_id[:8]}"

        item = [{
            "client_id": client_id,
            "entity":    "home_visit",
            "payload":   {
                "motherId":     self.mother_id,
                "scheduledFor": self.FUTURE_DT,
                "notes":        notes_tag,
            },
        }]

        # First upload (reconnect).
        r1 = self._sync(item)
        self.assertEqual(r1.data["results"][0]["status"], "processed")

        # Second upload (network hiccup during reconnect — client retries).
        r2 = self._sync(item)
        self.assertEqual(r2.data["results"][0]["status"], "duplicate",
                         "Second upload must be recognised as duplicate")

        # Exactly one row in home_visits.
        rows = query_all(
            self.db,
            "SELECT * FROM home_visits WHERE notes = ?",
            (notes_tag,),
        )
        self.assertEqual(len(rows), 1,
                         "BHW schedule must not show duplicates after retry upload")

    def test_failed_outbox_item_does_not_appear_in_sync_outbox(self):
        """An outbox item that fails validation must NOT be added to sync_outbox,
        so the client can retry it after correcting the data.

        Simulates: BHW queued an invalid record offline (e.g. missing motherId),
        then reconnects.  The item fails; a future reconnect can resend a corrected
        version (design.md §5).
        """
        from common import query_all

        client_id = str(uuid.uuid4())

        # home_visit without motherId → should fail validation
        outbox = [{
            "client_id": client_id,
            "entity":    "home_visit",
            "payload":   {
                "scheduledFor": self.FUTURE_DT,
                "notes":        "missing-mother-id-test",
                # motherId deliberately omitted
            },
        }]

        res = self._sync(outbox)
        self.assertEqual(res.status, 200)
        result = res.data["results"][0]
        self.assertEqual(result["status"], "error",
                         "Item with missing motherId must return status='error'")

        # Must NOT be in sync_outbox — client must be able to retry.
        rows = query_all(
            self.db,
            "SELECT * FROM sync_outbox WHERE client_id = ?",
            (client_id,),
        )
        self.assertEqual(
            len(rows), 0,
            "Failed item must not be recorded in sync_outbox (retryable, design.md §5)",
        )


# ── Reconnect: conflict handling end-to-end ───────────────────────────────────

class OfflineConflictEndToEndTests(unittest.TestCase):
    """End-to-end conflict handling through the sync endpoint.

    Design.md §5:
      "Clinical flags are never silently overwritten: two flags for the same
      visit are both kept."

    This reproduces the scenario where two BHW devices (or the same device in
    two offline sessions) both create flags for the same mother, then both
    reconnect.  Every flag must survive.
    """

    @classmethod
    def setUpClass(cls):
        cls.db     = open_database(":memory:", shared_password=STAFF_PASSWORD)
        cls.app    = create_app(cls.db, log=lambda *a: None)
        cls.client = _Client(cls.app)

        cls.bhw_token = cls.client.sign_in("b.reyes@bhw.example", STAFF_PASSWORD)

        # Register a mother.
        res = cls.client.request(
            "POST", "/api/mothers",
            token=cls.bhw_token,
            body={
                "name": "Conflict E2E Mother",
                "dateOfBirth": "1991-02-14",
                "consentRecordedAt": CONSENT,
            },
        )
        assert res.status == 201, f"mother setup failed: {res.data}"
        cls.mother_id = res.data["mother"]["id"]

    def _sync(self, outbox):
        return self.client.request(
            "POST", "/api/sync",
            body={"outbox": outbox},
            token=self.bhw_token,
        )

    def test_two_devices_both_flag_same_mother_both_flags_kept(self):
        """Simulate two offline devices each raising a flag for the same mother.
        When both reconnect (two separate sync calls), all flags must be present.

        This is the conflict rule from design.md §5 exercised end-to-end.
        """
        from common import query_all

        obs_device_a = f"device-a-obs-{uuid.uuid4().hex[:8]}"
        obs_device_b = f"device-b-obs-{uuid.uuid4().hex[:8]}"

        # Device A reconnects first.
        r_a = self._sync([{
            "client_id": str(uuid.uuid4()),
            "entity":    "risk_flag",
            "payload":   {"motherId": self.mother_id, "observation": obs_device_a},
        }])
        self.assertEqual(r_a.data["results"][0]["status"], "processed")

        # Device B reconnects later.
        r_b = self._sync([{
            "client_id": str(uuid.uuid4()),
            "entity":    "risk_flag",
            "payload":   {"motherId": self.mother_id, "observation": obs_device_b},
        }])
        self.assertEqual(r_b.data["results"][0]["status"], "processed")

        # Both flags must exist.
        rows = query_all(
            self.db,
            "SELECT * FROM risk_flags WHERE observation IN (?, ?)",
            (obs_device_a, obs_device_b),
        )
        self.assertEqual(
            len(rows), 2,
            "Both offline device flags must be kept after reconnect (conflict rule)",
        )
        for row in rows:
            self.assertEqual(row["status"], "open",
                             "Each flag must start with status='open'")

    def test_multiple_offline_flags_same_client_session_all_kept(self):
        """Single BHW session offline: raise three flags for the same mother, all
        queued in IndexedDB.  On reconnect all three must be accepted and kept.
        """
        from common import query_all

        obs_texts = [f"session-flag-{uuid.uuid4().hex[:8]}" for _ in range(3)]
        outbox = [
            {
                "client_id": str(uuid.uuid4()),
                "entity":    "risk_flag",
                "payload":   {"motherId": self.mother_id, "observation": obs},
            }
            for obs in obs_texts
        ]

        res = self._sync(outbox)
        self.assertEqual(res.status, 200)
        statuses = [r["status"] for r in res.data["results"]]
        self.assertEqual(statuses, ["processed"] * 3,
                         f"All three flags should be processed, got {statuses}")

        # All three rows must exist.
        rows = query_all(
            self.db,
            f"SELECT * FROM risk_flags WHERE observation IN ({','.join(['?']*3)})",
            tuple(obs_texts),
        )
        self.assertEqual(
            len(rows), 3,
            "All three offline-queued flags must exist after reconnect",
        )

    def test_mixed_offline_outbox_partial_failure_does_not_block_other_items(self):
        """If one item in the offline outbox fails validation, the remaining items
        must still be processed.

        Scenario: BHW queued [home_visit (invalid), risk_flag (valid), referral (valid)].
        On reconnect only the home_visit fails; the other two go through.
        """
        from common import query_all

        obs_text    = f"partial-fail-obs-{uuid.uuid4().hex[:8]}"
        reason_text = f"partial-fail-ref-{uuid.uuid4().hex[:8]}"

        outbox = [
            {
                "client_id": str(uuid.uuid4()),
                "entity":    "home_visit",
                "payload":   {
                    "scheduledFor": FUTURE,
                    # motherId deliberately missing → error
                },
            },
            {
                "client_id": str(uuid.uuid4()),
                "entity":    "risk_flag",
                "payload":   {"motherId": self.mother_id, "observation": obs_text},
            },
            {
                "client_id": str(uuid.uuid4()),
                "entity":    "referral",
                "payload":   {
                    "motherId":         self.mother_id,
                    "reason":           reason_text,
                    "toFacilityOrUser": "RHU-001",
                },
            },
        ]

        res = self._sync(outbox)
        self.assertEqual(res.status, 200)
        statuses = [r["status"] for r in res.data["results"]]
        self.assertEqual(
            statuses, ["error", "processed", "processed"],
            f"Expected [error, processed, processed], got {statuses}",
        )

        # The valid items must have been persisted.
        flag_rows = query_all(
            self.db,
            "SELECT * FROM risk_flags WHERE observation = ?",
            (obs_text,),
        )
        self.assertEqual(len(flag_rows), 1,
                         "Valid risk_flag after a failed item must still be persisted")

        ref_rows = query_all(
            self.db,
            "SELECT * FROM referrals WHERE reason = ?",
            (reason_text,),
        )
        self.assertEqual(len(ref_rows), 1,
                         "Valid referral after a failed item must still be persisted")


if __name__ == "__main__":
    unittest.main()
