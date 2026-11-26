"""Tests for Phase 6, task 6.2: referral create and status-change endpoints.

Covers:
  - POST /api/referrals              — create (happy path, missing fields, 401, 403)
  - GET  /api/referrals              — list (with and without filters)
  - GET  /api/referrals/:id          — get single referral with events
  - PATCH /api/referrals/:id/status  — valid transitions, skip-step returns 400,
                                        close without note returns 400, 401, 403

Run with: py -m unittest discover -s tests
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

from app import create_app    # noqa: E402
from db  import open_database  # noqa: E402

STAFF_PASSWORD = "test-only-6.2-pass"
CONSENT = "2026-10-01T08:00:00Z"


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

class ReferralEndpointTests(unittest.TestCase):

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

        # Register a mother used throughout
        res = cls.cli.request(
            "POST", "/api/mothers",
            token=cls.tok["midwife"],
            body={"name": "Referral Test Mother",
                  "dateOfBirth": "1992-03-15",
                  "consentRecordedAt": CONSENT},
        )
        assert res.status == 201, f"mother setup failed: {res.data}"
        cls.mid = res.data["mother"]["id"]

    def req(self, method, path, **kwargs):
        return self.cli.request(method, path, **kwargs)

    def _create_referral(self, token=None, **extra_body):
        token = token or self.tok["bhw"]
        body = {"motherId": self.mid, "reason": "High blood pressure observed", **extra_body}
        res = self.req("POST", "/api/referrals", token=token, body=body)
        return res

    # ── POST /api/referrals ───────────────────────────────────────────────────

    def test_bhw_can_create_referral(self):
        res = self._create_referral(token=self.tok["bhw"])
        self.assertEqual(res.status, 201, res.data)
        r = res.data["referral"]
        self.assertEqual(r["status"], "pending")
        self.assertEqual(r["mother_id"], self.mid)

    def test_midwife_can_create_referral(self):
        res = self._create_referral(token=self.tok["midwife"])
        self.assertEqual(res.status, 201, res.data)

    def test_physician_can_create_referral(self):
        res = self._create_referral(token=self.tok["physician"])
        self.assertEqual(res.status, 201, res.data)

    def test_rhu_admin_can_create_referral(self):
        res = self._create_referral(token=self.tok["rhu_admin"])
        self.assertEqual(res.status, 201, res.data)

    def test_sysadmin_cannot_create_referral_returns_403(self):
        """sysadmin is not in REFERRAL_WRITE_ROLES."""
        res = self._create_referral(token=self.tok["sysadmin"])
        self.assertEqual(res.status, 403)

    def test_unauthenticated_create_returns_401(self):
        res = self.req("POST", "/api/referrals",
                       body={"motherId": self.mid, "reason": "Test"})
        self.assertEqual(res.status, 401)

    def test_create_missing_mother_id_returns_400(self):
        res = self.req("POST", "/api/referrals",
                       token=self.tok["bhw"],
                       body={"reason": "No mother_id supplied"})
        self.assertEqual(res.status, 400)

    def test_create_missing_reason_returns_400(self):
        res = self.req("POST", "/api/referrals",
                       token=self.tok["bhw"],
                       body={"motherId": self.mid})
        self.assertEqual(res.status, 400)

    # ── GET /api/referrals ────────────────────────────────────────────────────

    def test_list_referrals_returns_list(self):
        self._create_referral()
        res = self.req("GET", "/api/referrals", token=self.tok["midwife"])
        self.assertEqual(res.status, 200, res.data)
        self.assertIn("referrals", res.data)
        self.assertIsInstance(res.data["referrals"], list)

    def test_list_referrals_filter_by_mother(self):
        self._create_referral()
        res = self.req("GET", f"/api/referrals?mother_id={self.mid}",
                       token=self.tok["physician"])
        self.assertEqual(res.status, 200)
        for r in res.data["referrals"]:
            self.assertEqual(r["mother_id"], self.mid)

    def test_list_referrals_filter_by_status(self):
        self._create_referral()
        res = self.req("GET", "/api/referrals?status=pending",
                       token=self.tok["physician"])
        self.assertEqual(res.status, 200)
        for r in res.data["referrals"]:
            self.assertEqual(r["status"], "pending")

    def test_list_unauthenticated_returns_401(self):
        res = self.req("GET", "/api/referrals")
        self.assertEqual(res.status, 401)

    # ── GET /api/referrals/:id ────────────────────────────────────────────────

    def test_get_single_referral_returns_referral_and_events(self):
        create_res = self._create_referral()
        self.assertEqual(create_res.status, 201)
        rid = create_res.data["referral"]["id"]

        res = self.req("GET", f"/api/referrals/{rid}", token=self.tok["midwife"])
        self.assertEqual(res.status, 200, res.data)
        self.assertIn("referral", res.data)
        self.assertIn("events", res.data)
        # The initial 'pending' event is always recorded on creation.
        self.assertGreaterEqual(len(res.data["events"]), 1)
        self.assertEqual(res.data["events"][0]["status"], "pending")

    def test_get_nonexistent_referral_returns_404(self):
        res = self.req("GET", "/api/referrals/999999", token=self.tok["midwife"])
        self.assertEqual(res.status, 404)

    def test_get_referral_unauthenticated_returns_401(self):
        res = self.req("GET", "/api/referrals/1")
        self.assertEqual(res.status, 401)

    # ── PATCH /api/referrals/:id/status ──────────────────────────────────────

    def _fresh_referral_id(self):
        """Create a brand-new referral (pending) and return its id."""
        res = self._create_referral()
        self.assertEqual(res.status, 201)
        return res.data["referral"]["id"]

    def _advance(self, rid, new_status, note="", token=None):
        token = token or self.tok["physician"]
        return self.req("PATCH", f"/api/referrals/{rid}/status",
                        token=token,
                        body={"status": new_status, "note": note})

    # --- Valid one-step transitions ---

    def test_pending_to_acknowledged_succeeds(self):
        rid = self._fresh_referral_id()
        res = self._advance(rid, "acknowledged")
        self.assertEqual(res.status, 200, res.data)
        self.assertEqual(res.data["referral"]["status"], "acknowledged")
        # Event row appended
        self.assertEqual(res.data["event"]["status"], "acknowledged")

    def test_acknowledged_to_attended_succeeds(self):
        rid = self._fresh_referral_id()
        self._advance(rid, "acknowledged")
        res = self._advance(rid, "attended")
        self.assertEqual(res.status, 200, res.data)
        self.assertEqual(res.data["referral"]["status"], "attended")

    def test_attended_to_closed_with_note_succeeds(self):
        rid = self._fresh_referral_id()
        self._advance(rid, "acknowledged")
        self._advance(rid, "attended")
        res = self._advance(rid, "closed", note="Patient seen and discharged.")
        self.assertEqual(res.status, 200, res.data)
        self.assertEqual(res.data["referral"]["status"], "closed")

    # --- Skipping a step returns 400 ---

    def test_skip_pending_to_attended_returns_400(self):
        rid = self._fresh_referral_id()
        res = self._advance(rid, "attended")
        self.assertEqual(res.status, 400, res.data)

    def test_skip_pending_to_closed_returns_400(self):
        rid = self._fresh_referral_id()
        res = self._advance(rid, "closed")
        self.assertEqual(res.status, 400, res.data)

    def test_skip_acknowledged_to_closed_returns_400(self):
        rid = self._fresh_referral_id()
        self._advance(rid, "acknowledged")
        res = self._advance(rid, "closed")
        self.assertEqual(res.status, 400, res.data)

    def test_going_backwards_pending_to_pending_returns_400(self):
        rid = self._fresh_referral_id()
        res = self._advance(rid, "pending")
        self.assertEqual(res.status, 400, res.data)

    def test_already_closed_cannot_be_advanced_returns_400(self):
        rid = self._fresh_referral_id()
        self._advance(rid, "acknowledged")
        self._advance(rid, "attended")
        self._advance(rid, "closed", note="Done.")
        res = self._advance(rid, "closed", note="Again.")
        self.assertEqual(res.status, 400, res.data)

    # --- Close without note returns 400 (task 6.3) ---

    def test_close_without_note_returns_400(self):
        rid = self._fresh_referral_id()
        self._advance(rid, "acknowledged")
        self._advance(rid, "attended")
        res = self._advance(rid, "closed", note="")
        self.assertEqual(res.status, 400, res.data)

    def test_close_with_whitespace_only_note_returns_400(self):
        rid = self._fresh_referral_id()
        self._advance(rid, "acknowledged")
        self._advance(rid, "attended")
        res = self._advance(rid, "closed", note="   ")
        self.assertEqual(res.status, 400, res.data)

    # --- Role checks ---

    def test_bhw_cannot_advance_status_returns_403(self):
        """bhw is not in REFERRAL_REVIEW_ROLES."""
        rid = self._fresh_referral_id()
        res = self._advance(rid, "acknowledged", token=self.tok["bhw"])
        self.assertEqual(res.status, 403)

    def test_midwife_can_advance_status(self):
        rid = self._fresh_referral_id()
        res = self._advance(rid, "acknowledged", token=self.tok["midwife"])
        self.assertEqual(res.status, 200, res.data)

    def test_rhu_admin_can_advance_status(self):
        rid = self._fresh_referral_id()
        res = self._advance(rid, "acknowledged", token=self.tok["rhu_admin"])
        self.assertEqual(res.status, 200, res.data)

    def test_unauthenticated_advance_returns_401(self):
        rid = self._fresh_referral_id()
        res = self.req("PATCH", f"/api/referrals/{rid}/status",
                       body={"status": "acknowledged"})
        self.assertEqual(res.status, 401)

    # --- Nonexistent referral ---

    def test_advance_nonexistent_referral_returns_404(self):
        res = self._advance(999999, "acknowledged")
        self.assertEqual(res.status, 404)

    # --- Events are append-only after full lifecycle ---

    def test_full_lifecycle_creates_four_events(self):
        rid = self._fresh_referral_id()
        self._advance(rid, "acknowledged")
        self._advance(rid, "attended")
        self._advance(rid, "closed", note="All good.")

        res = self.req("GET", f"/api/referrals/{rid}", token=self.tok["physician"])
        self.assertEqual(res.status, 200)
        statuses = [e["status"] for e in res.data["events"]]
        # Initial creation event + 3 advances = 4 total
        self.assertEqual(statuses, ["pending", "acknowledged", "attended", "closed"])

    # --- Invalid status value ---

    def test_invalid_status_string_returns_400(self):
        rid = self._fresh_referral_id()
        res = self._advance(rid, "in_progress")
        self.assertEqual(res.status, 400, res.data)

    # --- Audit log written ---

    def test_audit_row_written_on_status_advance(self):
        from common import query_all as qa
        rid = self._fresh_referral_id()
        self._advance(rid, "acknowledged")
        rows = qa(self.db,
                  "SELECT * FROM audit_log WHERE action = 'referral_status_updated' ORDER BY id DESC LIMIT 1")
        self.assertTrue(len(rows) >= 1, "Expected audit row for referral_status_updated")


if __name__ == "__main__":
    unittest.main()


# ── Task 6.5: Follow-up list ──────────────────────────────────────────────────

class TestFollowUpList(unittest.TestCase):
    """list_mothers_with_open_referrals and GET /api/follow-up (task 6.5).

    Verifies:
    - A mother with a pending referral appears in the follow-up list.
    - A mother whose only referral is closed does NOT appear in the list.
    - A mother with one open and one closed referral still appears (open one shown).
    - GET /api/follow-up returns 401 for unauthenticated requests.
    - GET /api/follow-up returns 403 for roles that are not allowed (e.g. sysadmin).
    - GET /api/follow-up returns the followUp list for allowed roles.
    """

    STAFF_PASSWORD = "test-only-6.5"

    @classmethod
    def setUpClass(cls):
        from app import create_app
        from db import open_database
        import referrals as referrals_module
        cls._referrals_module = referrals_module

        def _first_user(db):
            from common import query_one as _qone
            return _qone(db, "SELECT * FROM users LIMIT 1")

        cls.db = open_database(":memory:", shared_password=cls.STAFF_PASSWORD)
        cls.app = create_app(cls.db, log=lambda *a: None)

        class _Result:
            def __init__(self, status, content_type, body, headers):
                import json as _json
                self.status = status
                self.data = _json.loads(body) if content_type and "json" in content_type else body

        class _Client:
            def __init__(self, app):
                self.app = app

            def request(self, method, path, body=None, token=None):
                import json as _json
                headers = {}
                raw = b""
                if body is not None:
                    raw = _json.dumps(body).encode()
                    headers["content-type"] = "application/json"
                if token:
                    headers["authorization"] = "Bearer " + token
                return _Result(*self.app.handle(method, path, headers, raw))

            def sign_in(self, email, password):
                res = self.request("POST", "/api/login",
                                   body={"email": email, "password": password})
                assert res.status == 200, f"login failed for {email}: {res.data}"
                return res.data["token"]

        cls.client = _Client(cls.app)
        cls.tok = {
            "bhw":       cls.client.sign_in("b.reyes@bhw.example",   cls.STAFF_PASSWORD),
            "midwife":   cls.client.sign_in("c.lim@rhu.example",     cls.STAFF_PASSWORD),
            "physician": cls.client.sign_in("a.okafor@rhu.example",  cls.STAFF_PASSWORD),
            "rhu_admin": cls.client.sign_in("rhu.admin@rhu.example", cls.STAFF_PASSWORD),
            "sysadmin":  cls.client.sign_in("admin@example.com",     cls.STAFF_PASSWORD),
        }

        CONSENT = "2026-10-01T08:00:00Z"

        # Mother A — will have one open (pending) referral
        res = cls.client.request(
            "POST", "/api/mothers", token=cls.tok["midwife"],
            body={"name": "Follow-up Test A", "dateOfBirth": "1990-03-01",
                  "consentRecordedAt": CONSENT},
        )
        assert res.status == 201, res.data
        cls.mid_a = res.data["mother"]["id"]

        # Mother B — will have only a closed referral
        res = cls.client.request(
            "POST", "/api/mothers", token=cls.tok["midwife"],
            body={"name": "Follow-up Test B", "dateOfBirth": "1991-04-01",
                  "consentRecordedAt": CONSENT},
        )
        assert res.status == 201, res.data
        cls.mid_b = res.data["mother"]["id"]

        # Mother C — will have one open + one closed referral
        res = cls.client.request(
            "POST", "/api/mothers", token=cls.tok["midwife"],
            body={"name": "Follow-up Test C", "dateOfBirth": "1992-05-01",
                  "consentRecordedAt": CONSENT},
        )
        assert res.status == 201, res.data
        cls.mid_c = res.data["mother"]["id"]

        # Look up the midwife user row to use as the actor for referral creation
        from common import query_one as _q
        actor = _q(cls.db, "SELECT * FROM users WHERE email = 'c.lim@rhu.example'")
        assert actor, "midwife seed user not found"

        # Referral for Mother A — stays pending (open)
        ref_a = referrals_module.create_referral(cls.db, actor, {
            "mother_id": cls.mid_a,
            "reason": "Elevated blood pressure",
        })
        assert "referral" in ref_a, ref_a
        cls.ref_a_id = ref_a["referral"]["id"]

        # Referral for Mother B — immediately closed
        ref_b = referrals_module.create_referral(cls.db, actor, {
            "mother_id": cls.mid_b,
            "reason": "Routine follow-up",
        })
        assert "referral" in ref_b, ref_b
        cls.ref_b_id = ref_b["referral"]["id"]
        # Close it directly in the DB (bypassing the status-order check for setup simplicity)
        cls.db.execute(
            "UPDATE referrals SET status = 'closed', updated_at = datetime('now') WHERE id = ?",
            (cls.ref_b_id,),
        )

        # Two referrals for Mother C: one open, one closed
        ref_c_open = referrals_module.create_referral(cls.db, actor, {
            "mother_id": cls.mid_c,
            "reason": "Oedema observed",
        })
        assert "referral" in ref_c_open, ref_c_open
        cls.ref_c_open_id = ref_c_open["referral"]["id"]

        ref_c_closed = referrals_module.create_referral(cls.db, actor, {
            "mother_id": cls.mid_c,
            "reason": "Earlier concern resolved",
        })
        assert "referral" in ref_c_closed, ref_c_closed
        cls.db.execute(
            "UPDATE referrals SET status = 'closed', updated_at = datetime('now') WHERE id = ?",
            (ref_c_closed["referral"]["id"],),
        )

    def _ids_on_list(self):
        """Return the set of mother_ids currently on the follow-up list."""
        referrals_module = self.__class__._referrals_module
        entries = referrals_module.list_mothers_with_open_referrals(self.db)
        return {e["mother_id"] for e in entries}

    # ── Unit-level tests (direct function calls) ──────────────────────────────

    def test_mother_with_pending_referral_is_on_follow_up_list(self):
        self.assertIn(self.mid_a, self._ids_on_list())

    def test_mother_with_only_closed_referral_is_not_on_follow_up_list(self):
        self.assertNotIn(self.mid_b, self._ids_on_list())

    def test_mother_with_one_open_and_one_closed_referral_is_on_list(self):
        self.assertIn(self.mid_c, self._ids_on_list())

    def test_open_referral_summary_is_included_in_entry(self):
        referrals_module = self.__class__._referrals_module
        entries = referrals_module.list_mothers_with_open_referrals(self.db)
        entry_a = next((e for e in entries if e["mother_id"] == self.mid_a), None)
        self.assertIsNotNone(entry_a)
        self.assertTrue(len(entry_a["open_referrals"]) >= 1)
        statuses = {r["status"] for r in entry_a["open_referrals"]}
        self.assertNotIn("closed", statuses)

    def test_closed_referral_is_not_included_in_open_referrals_summary(self):
        referrals_module = self.__class__._referrals_module
        entries = referrals_module.list_mothers_with_open_referrals(self.db)
        entry_c = next((e for e in entries if e["mother_id"] == self.mid_c), None)
        self.assertIsNotNone(entry_c)
        statuses = [r["status"] for r in entry_c["open_referrals"]]
        self.assertNotIn("closed", statuses)

    # ── HTTP route tests ──────────────────────────────────────────────────────

    def _get(self, token=None):
        headers = {}
        raw = b""
        if token:
            headers["authorization"] = "Bearer " + token
        import json as _json
        from app import App

        class _Res:
            pass

        status, ct, body, _ = self.app.handle("GET", "/api/follow-up", headers, raw)
        res = _Res()
        res.status = status
        res.data = _json.loads(body) if ct and "json" in ct else body
        return res

    def test_unauthenticated_returns_401(self):
        self.assertEqual(self._get().status, 401)

    def test_sysadmin_returns_403(self):
        """sysadmin is not in REFERRAL_WRITE_ROLES and must be refused."""
        self.assertEqual(self._get(self.tok["sysadmin"]).status, 403)

    def test_bhw_can_access_follow_up_list(self):
        res = self._get(self.tok["bhw"])
        self.assertEqual(res.status, 200)
        self.assertIn("followUp", res.data)
        self.assertIsInstance(res.data["followUp"], list)

    def test_midwife_can_access_follow_up_list(self):
        res = self._get(self.tok["midwife"])
        self.assertEqual(res.status, 200)
        self.assertIn("followUp", res.data)

    def test_rhu_admin_can_access_follow_up_list(self):
        res = self._get(self.tok["rhu_admin"])
        self.assertEqual(res.status, 200)
        self.assertIn("followUp", res.data)

    def test_follow_up_list_contains_mother_with_open_referral(self):
        res = self._get(self.tok["midwife"])
        ids = {e["mother_id"] for e in res.data["followUp"]}
        self.assertIn(self.mid_a, ids)

    def test_follow_up_list_excludes_mother_with_only_closed_referral(self):
        res = self._get(self.tok["midwife"])
        ids = {e["mother_id"] for e in res.data["followUp"]}
        self.assertNotIn(self.mid_b, ids)


# ── Task 6.4: overdue referrals ───────────────────────────────────────────────

from datetime import datetime, timedelta, timezone

import referrals as referrals_module   # noqa: E402 (server/ is on sys.path from above)


class _ApiClient:
    """Minimal HTTP client for in-memory App calls."""

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
        status, ct, body_bytes, _ = self.app.handle(method, path, headers, raw)
        data = json.loads(body_bytes) if ct and "json" in ct else body_bytes
        return status, data

    def sign_in(self, email, password):
        status, data = self.request("POST", "/api/login",
                                    body={"email": email, "password": password})
        assert status == 200, f"login failed for {email}: {data}"
        return data["token"]


class TestOverdueReferrals(unittest.TestCase):
    """list_overdue_referrals and GET /api/referrals/overdue (task 6.4)."""

    STAFF_PASSWORD = "test-only-6.4-pass"

    @classmethod
    def setUpClass(cls):
        cls.db     = open_database(":memory:", shared_password=cls.STAFF_PASSWORD)
        cls.app    = create_app(cls.db, log=lambda *a: None)
        cls.client = _ApiClient(cls.app)

        cls.tok = {
            "rhu_admin": cls.client.sign_in("rhu.admin@rhu.example", cls.STAFF_PASSWORD),
            "physician": cls.client.sign_in("a.okafor@rhu.example",  cls.STAFF_PASSWORD),
            "midwife":   cls.client.sign_in("c.lim@rhu.example",     cls.STAFF_PASSWORD),
            "bhw":       cls.client.sign_in("b.reyes@bhw.example",   cls.STAFF_PASSWORD),
            "sysadmin":  cls.client.sign_in("admin@example.com",     cls.STAFF_PASSWORD),
        }

        # Create a mother to attach referrals to
        from common import query_one
        user = query_one(cls.db, "SELECT * FROM users LIMIT 1")
        cursor = cls.db.execute(
            """INSERT INTO mothers (name, assigned_bhw_id, status, duplicate_key,
                                   consent_recorded_at, created_by)
               VALUES ('Overdue Test Mother', ?, 'prenatal', 'dk-6.4-ot',
                       datetime('now'), ?)""",
            (user["id"], user["id"]),
        )
        cls.mother_id = cursor.lastrowid
        cls.user = user

    # ── Unit-level tests (call list_overdue_referrals directly) ──────────────

    def _insert_referral(self, status="pending", due_by=None, created_at=None):
        """Helper: insert a referral row directly, bypassing HTTP."""
        cursor = self.db.execute(
            """INSERT INTO referrals
               (mother_id, from_user_id, reason, status, due_by, created_at, created_by,
                updated_at)
               VALUES (?, ?, 'test reason', ?, ?,
                       COALESCE(?, datetime('now')), ?, datetime('now'))""",
            (self.mother_id, self.user["id"], status, due_by, created_at, self.user["id"]),
        )
        return cursor.lastrowid

    def test_pending_past_due_by_is_overdue(self):
        """A pending referral whose due_by is in the past must appear in the list."""
        past_due_by = "2000-01-01 10:00:00"
        ref_id = self._insert_referral(status="pending", due_by=past_due_by)

        now = datetime(2025, 6, 1, tzinfo=timezone.utc)
        result = referrals_module.list_overdue_referrals(self.db, now=now)
        ids = [r["id"] for r in result]
        self.assertIn(ref_id, ids, "pending referral with past due_by must be in overdue list")

    def test_pending_future_due_by_is_not_overdue(self):
        """A pending referral whose due_by is in the future must NOT appear."""
        future_due_by = "2099-12-31 23:59:00"
        ref_id = self._insert_referral(status="pending", due_by=future_due_by)

        now = datetime(2025, 6, 1, tzinfo=timezone.utc)
        result = referrals_module.list_overdue_referrals(self.db, now=now)
        ids = [r["id"] for r in result]
        self.assertNotIn(ref_id, ids, "pending referral with future due_by must NOT be overdue")

    def test_acknowledged_referral_is_not_overdue(self):
        """An acknowledged referral (no longer pending) must not appear even if past due_by."""
        past_due_by = "2000-01-01 10:00:00"
        ref_id = self._insert_referral(status="acknowledged", due_by=past_due_by)

        now = datetime(2025, 6, 1, tzinfo=timezone.utc)
        result = referrals_module.list_overdue_referrals(self.db, now=now)
        ids = [r["id"] for r in result]
        self.assertNotIn(ref_id, ids, "non-pending referral must never appear as overdue")

    def test_pending_no_due_by_exceeds_default_window_is_overdue(self):
        """Pending referral with no due_by, created long ago, falls back to DEFAULT_OVERDUE_HOURS."""
        # created_at far in the past; well past the 48 h default window
        old_created = "2000-01-01 00:00:00"
        ref_id = self._insert_referral(status="pending", due_by=None, created_at=old_created)

        now = datetime(2025, 6, 1, tzinfo=timezone.utc)
        result = referrals_module.list_overdue_referrals(
            self.db, now=now,
            overdue_hours=referrals_module.DEFAULT_OVERDUE_HOURS,
        )
        ids = [r["id"] for r in result]
        self.assertIn(ref_id, ids, "old pending referral with no due_by must appear as overdue")

    def test_pending_no_due_by_within_default_window_is_not_overdue(self):
        """Pending referral created 1 hour ago with no due_by must not be overdue (within 48 h)."""
        from common import sql_time, utc_now as _utc
        created_1h_ago = sql_time(_utc() - timedelta(hours=1))
        ref_id = self._insert_referral(status="pending", due_by=None, created_at=created_1h_ago)

        now = datetime.now(timezone.utc)
        result = referrals_module.list_overdue_referrals(
            self.db, now=now,
            overdue_hours=referrals_module.DEFAULT_OVERDUE_HOURS,
        )
        ids = [r["id"] for r in result]
        self.assertNotIn(
            ref_id, ids,
            "pending referral created 1 hour ago must NOT be overdue within 48 h window",
        )

    def test_overdue_hours_parameter_is_respected(self):
        """Passing a custom overdue_hours changes what counts as overdue."""
        from common import sql_time, utc_now as _utc
        # Created 25 hours ago
        created_25h_ago = sql_time(_utc() - timedelta(hours=25))
        ref_id = self._insert_referral(status="pending", due_by=None,
                                       created_at=created_25h_ago)

        now = datetime.now(timezone.utc)
        # With a 48 h window (default) this should NOT be overdue
        not_yet = referrals_module.list_overdue_referrals(self.db, now=now, overdue_hours=48)
        self.assertNotIn(ref_id, [r["id"] for r in not_yet],
                         "25 h old referral must not be overdue with 48 h window")

        # With a 24 h window it SHOULD be overdue
        already = referrals_module.list_overdue_referrals(self.db, now=now, overdue_hours=24)
        self.assertIn(ref_id, [r["id"] for r in already],
                      "25 h old referral must be overdue with 24 h window")

    # ── HTTP-layer tests ──────────────────────────────────────────────────────

    def test_unauthenticated_request_returns_401(self):
        status, _ = self.client.request("GET", "/api/referrals/overdue")
        self.assertEqual(status, 401)

    def test_bhw_role_cannot_access_overdue_endpoint(self):
        """BHW does not have access to the RHU dashboard overdue list."""
        status, _ = self.client.request("GET", "/api/referrals/overdue",
                                         token=self.tok["bhw"])
        self.assertEqual(status, 403)

    def test_sysadmin_cannot_access_overdue_endpoint(self):
        """sysadmin has no clinical access."""
        status, _ = self.client.request("GET", "/api/referrals/overdue",
                                         token=self.tok["sysadmin"])
        self.assertEqual(status, 403)

    def test_rhu_admin_can_access_overdue_endpoint(self):
        status, data = self.client.request("GET", "/api/referrals/overdue",
                                            token=self.tok["rhu_admin"])
        self.assertEqual(status, 200, data)
        self.assertIn("referrals", data)

    def test_physician_can_access_overdue_endpoint(self):
        status, data = self.client.request("GET", "/api/referrals/overdue",
                                            token=self.tok["physician"])
        self.assertEqual(status, 200, data)

    def test_midwife_can_access_overdue_endpoint(self):
        status, data = self.client.request("GET", "/api/referrals/overdue",
                                            token=self.tok["midwife"])
        self.assertEqual(status, 200, data)

    def test_overdue_endpoint_returns_only_pending_past_due(self):
        """The endpoint returns a pending+overdue referral and not a future one."""
        # Overdue: due_by already passed
        past_due_by = "2000-06-01 08:00:00"
        past_ref_id = self._insert_referral(status="pending", due_by=past_due_by)
        # Not overdue: due_by in the far future
        future_due_by = "2099-06-01 08:00:00"
        future_ref_id = self._insert_referral(status="pending", due_by=future_due_by)

        status, data = self.client.request("GET", "/api/referrals/overdue",
                                            token=self.tok["rhu_admin"])
        self.assertEqual(status, 200, data)
        ids = [r["id"] for r in data["referrals"]]
        self.assertIn(past_ref_id, ids,
                      "overdue referral must appear in /api/referrals/overdue response")
        self.assertNotIn(future_ref_id, ids,
                         "future referral must NOT appear in /api/referrals/overdue response")


if __name__ == "__main__":
    unittest.main()


# ── Task 6.1: Table creation and append-only enforcement ──────────────────────

class TestReferralTablesSetup(unittest.TestCase):
    """Verify the tables and database-level guards created by setup_referrals (task 6.1).

    Covers:
      - Both tables exist after setup_referrals is called.
      - referral_events UPDATE is blocked by trigger (append-only).
      - referral_events DELETE is blocked by trigger (append-only).
      - referrals.status CHECK constraint rejects values not in the allowed set.
    """

    @classmethod
    def setUpClass(cls):
        import sqlite3
        import sys
        from pathlib import Path
        ROOT = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(ROOT / "server"))
        from db import open_database
        # Use an in-memory database so there is no side-effect on data/caap.db
        cls.db = open_database(":memory:")

    # ── Both tables exist ─────────────────────────────────────────────────────

    def test_referrals_table_exists(self):
        from common import query_one
        row = query_one(
            self.db,
            "SELECT name FROM sqlite_master WHERE type='table' AND name='referrals'",
        )
        self.assertIsNotNone(row, "referrals table must exist after setup_referrals()")

    def test_referral_events_table_exists(self):
        from common import query_one
        row = query_one(
            self.db,
            "SELECT name FROM sqlite_master WHERE type='table' AND name='referral_events'",
        )
        self.assertIsNotNone(row, "referral_events table must exist after setup_referrals()")

    # ── Append-only triggers ──────────────────────────────────────────────────

    def _seed_event(self):
        """Insert a minimal referral + event row and return the event id."""
        import sqlite3
        import uuid
        from common import query_one

        user = query_one(self.db, "SELECT id FROM users LIMIT 1")
        self.assertIsNotNone(user, "seed users must exist")
        uid = user["id"]

        # Insert a mother row required by the foreign key
        # Use a unique duplicate_key for each test run
        dup_key = f"dup-key-trigger-test-{uuid.uuid4().hex}"
        cursor = self.db.execute(
            """INSERT INTO mothers (name, assigned_bhw_id, status, duplicate_key,
                                   consent_recorded_at, created_by)
               VALUES ('Trigger Test Mother', ?, 'prenatal', ?,
                       datetime('now'), ?)""",
            (uid, dup_key, uid),
        )
        mother_id = cursor.lastrowid

        cursor = self.db.execute(
            """INSERT INTO referrals (mother_id, from_user_id, reason, created_by)
               VALUES (?, ?, 'trigger test reason', ?)""",
            (mother_id, uid, uid),
        )
        referral_id = cursor.lastrowid

        cursor = self.db.execute(
            """INSERT INTO referral_events (referral_id, status, actor, note)
               VALUES (?, 'pending', ?, 'initial event')""",
            (referral_id, uid),
        )
        return cursor.lastrowid

    def test_update_on_referral_events_is_blocked(self):
        """The referral_events_no_update trigger must raise when UPDATE is attempted."""
        import sqlite3
        event_id = self._seed_event()
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self.db.execute(
                "UPDATE referral_events SET note = 'tampered' WHERE id = ?",
                (event_id,),
            )
        self.assertIn("append-only", str(ctx.exception).lower(),
                      "trigger error message must mention append-only")

    def test_delete_on_referral_events_is_blocked(self):
        """The referral_events_no_delete trigger must raise when DELETE is attempted."""
        import sqlite3
        event_id = self._seed_event()
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self.db.execute(
                "DELETE FROM referral_events WHERE id = ?",
                (event_id,),
            )
        self.assertIn("append-only", str(ctx.exception).lower(),
                      "trigger error message must mention append-only")

    # ── CHECK constraint on referrals.status ─────────────────────────────────

    def test_invalid_status_rejected_by_check_constraint(self):
        """Inserting an invalid status value into referrals must raise IntegrityError."""
        import sqlite3
        import uuid
        from common import query_one

        user = query_one(self.db, "SELECT id FROM users LIMIT 1")
        uid = user["id"]

        dup_key = f"dup-key-check-test-{uuid.uuid4().hex}"
        cursor = self.db.execute(
            """INSERT INTO mothers (name, assigned_bhw_id, status, duplicate_key,
                                   consent_recorded_at, created_by)
               VALUES ('Check Test Mother', ?, 'prenatal', ?,
                       datetime('now'), ?)""",
            (uid, dup_key, uid),
        )
        mother_id = cursor.lastrowid

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                """INSERT INTO referrals (mother_id, from_user_id, reason, status, created_by)
                   VALUES (?, ?, 'check test', 'invalid_status', ?)""",
                (mother_id, uid, uid),
            )

    def test_valid_status_values_are_accepted(self):
        """All four valid status values must insert without error."""
        import uuid
        from common import query_one

        user = query_one(self.db, "SELECT id FROM users LIMIT 1")
        uid = user["id"]

        for status in ("pending", "acknowledged", "attended", "closed"):
            dup_key = f"dup-key-valid-{status}-{uuid.uuid4().hex}"
            cursor = self.db.execute(
                """INSERT INTO mothers (name, assigned_bhw_id, status, duplicate_key,
                                       consent_recorded_at, created_by)
                   VALUES (?, ?, 'prenatal', ?, datetime('now'), ?)""",
                (f"Status Test Mother {status}", uid, dup_key, uid),
            )
            mother_id = cursor.lastrowid
            # Should not raise
            self.db.execute(
                """INSERT INTO referrals (mother_id, from_user_id, reason, status, created_by)
                   VALUES (?, ?, 'valid status test', ?, ?)""",
                (mother_id, uid, status, uid),
            )
