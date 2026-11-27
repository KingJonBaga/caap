"""Tests for task 9.1 — BHW and RHU dashboard endpoints.

GET /api/bhw/dashboard  — returns live counts for the signed-in BHW
GET /api/rhu/dashboard  — returns network-wide counts for RHU admin roles

Covers:
  - correct counts after inserting known data
  - access control (BHW only for bhw dashboard; rhu_admin/physician/midwife for RHU)
  - unauthenticated requests return 401
  - wrong-role requests return 403
  - two BHWs see only their own mothers / visits

Run with: py -m unittest discover -s tests
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAFF_PASSWORD = "test-only-staff-pass"
sys.path.insert(0, str(ROOT / "server"))

from app import create_app    # noqa: E402
from db import open_database  # noqa: E402


# ── Minimal test client ───────────────────────────────────────────────────────

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

    def sign_in(self, email, password=STAFF_PASSWORD):
        res = self.request("POST", "/api/login", body={"email": email, "password": password})
        assert res.status == 200, f"login failed for {email}: {res.data}"
        return res.data["token"]


CONSENT = "2026-10-01T08:00:00Z"
FUTURE  = "2099-01-01T10:00:00"
PAST    = "2020-01-01T10:00:00"


# ── Test setup helpers ────────────────────────────────────────────────────────

def _register_mother(client, token, name, bhw_id=None, status="prenatal"):
    body = {
        "name":              name,
        "consentRecordedAt": CONSENT,
        "assignedBhwId":     bhw_id,
    }
    res = client.request("POST", "/api/mothers", token=token, body=body)
    assert res.status == 201, f"mother creation failed: {res.data}"
    if status != "prenatal":
        # Update the status directly in the db if needed — not exposed via API
        pass
    return res.data["mother"]["id"]


def _make_home_visit(client, token, mother_id, scheduled_for):
    res = client.request(
        "POST", "/api/home-visits",
        token=token,
        body={"motherId": mother_id, "scheduledFor": scheduled_for},
    )
    assert res.status == 201, f"home visit creation failed: {res.data}"
    return res.data["visit"]["id"]


def _make_referral(client, token, mother_id):
    res = client.request(
        "POST", "/api/referrals",
        token=token,
        body={"mother_id": mother_id, "reason": "Elevated risk"},
    )
    assert res.status == 201, f"referral creation failed: {res.data}"
    return res.data["referral"]["id"]


def _make_flag(client, token, mother_id):
    res = client.request(
        "POST", f"/api/mothers/{mother_id}/observations",
        token=token,
        body={"observation": "Patient reported severe headache"},
    )
    assert res.status == 201, f"flag creation failed: {res.data}"
    return res.data["flag"]["id"]


# ── BHW dashboard tests ───────────────────────────────────────────────────────

class BhwDashboardTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db     = open_database(":memory:", shared_password=STAFF_PASSWORD)
        cls.app    = create_app(cls.db, log=lambda *_: None)
        cls.client = Client(cls.app)

        cls.tok = {
            "bhw":       cls.client.sign_in("b.reyes@bhw.example"),
            "midwife":   cls.client.sign_in("c.lim@rhu.example"),
            "physician": cls.client.sign_in("a.okafor@rhu.example"),
            "rhu_admin": cls.client.sign_in("rhu.admin@rhu.example"),
            "sysadmin":  cls.client.sign_in("admin@example.com"),
        }

        # Look up the BHW's user id
        me = cls.client.request("GET", "/api/me", token=cls.tok["bhw"])
        cls.bhw_id = me.data["user"]["id"]

    def req(self, method, path, **kwargs):
        return self.client.request(method, path, **kwargs)

    # ── Access control ────────────────────────────────────────────────────────

    def test_bhw_dashboard_requires_authentication(self):
        res = self.req("GET", "/api/bhw/dashboard")
        self.assertEqual(res.status, 401)

    def test_bhw_dashboard_forbidden_for_non_bhw_roles(self):
        for role in ("midwife", "physician", "rhu_admin", "sysadmin"):
            res = self.req("GET", "/api/bhw/dashboard", token=self.tok[role])
            self.assertEqual(res.status, 403, f"expected 403 for role '{role}'")

    def test_bhw_can_access_bhw_dashboard(self):
        res = self.req("GET", "/api/bhw/dashboard", token=self.tok["bhw"])
        self.assertEqual(res.status, 200)

    # ── Response shape ────────────────────────────────────────────────────────

    def test_bhw_dashboard_response_has_expected_keys(self):
        res = self.req("GET", "/api/bhw/dashboard", token=self.tok["bhw"])
        self.assertEqual(res.status, 200)
        self.assertIn("mothers",          res.data)
        self.assertIn("homeVisits",       res.data)
        self.assertIn("openFlags",        res.data)
        self.assertIn("pendingReferrals", res.data)

    def test_bhw_dashboard_mothers_has_status_keys(self):
        res = self.req("GET", "/api/bhw/dashboard", token=self.tok["bhw"])
        m = res.data["mothers"]
        self.assertIn("prenatal",   m)
        self.assertIn("postpartum", m)
        self.assertIn("closed",     m)
        self.assertIn("total",      m)

    def test_bhw_dashboard_home_visits_has_status_keys(self):
        res = self.req("GET", "/api/bhw/dashboard", token=self.tok["bhw"])
        hv = res.data["homeVisits"]
        for key in ("dueToday", "overdue", "missed", "completed", "rescheduled"):
            self.assertIn(key, hv)

    # ── Correct counts after inserting known data ─────────────────────────────

    def test_bhw_dashboard_counts_assigned_mothers(self):
        # Create a fresh db for this assertion to get a clean baseline
        db  = open_database(":memory:", shared_password=STAFF_PASSWORD)
        app = create_app(db, log=lambda *_: None)
        c   = Client(app)

        bhw_tok = c.sign_in("b.reyes@bhw.example")
        mid_tok = c.sign_in("c.lim@rhu.example")
        bhw_me  = c.request("GET", "/api/me", token=bhw_tok)
        bhw_uid = bhw_me.data["user"]["id"]

        # Zero to start
        res0 = c.request("GET", "/api/bhw/dashboard", token=bhw_tok)
        self.assertEqual(res0.data["mothers"]["total"], 0)

        # Register 2 mothers assigned to this BHW
        for i in range(2):
            c.request(
                "POST", "/api/mothers",
                token=mid_tok,
                body={
                    "name":              f"Test Mother {i}",
                    "consentRecordedAt": CONSENT,
                    "assignedBhwId":     bhw_uid,
                },
            )

        res1 = c.request("GET", "/api/bhw/dashboard", token=bhw_tok)
        self.assertEqual(res1.data["mothers"]["total"],    2)
        self.assertEqual(res1.data["mothers"]["prenatal"], 2)

    def test_bhw_dashboard_counts_home_visits_due_today(self):
        db  = open_database(":memory:", shared_password=STAFF_PASSWORD)
        app = create_app(db, log=lambda *_: None)
        c   = Client(app)

        bhw_tok = c.sign_in("b.reyes@bhw.example")
        mid_tok = c.sign_in("c.lim@rhu.example")
        bhw_me  = c.request("GET", "/api/me", token=bhw_tok)
        bhw_uid = bhw_me.data["user"]["id"]

        mother_res = c.request(
            "POST", "/api/mothers",
            token=mid_tok,
            body={"name": "Visit Mother", "consentRecordedAt": CONSENT, "assignedBhwId": bhw_uid},
        )
        mid = mother_res.data["mother"]["id"]

        # Schedule a future home visit
        c.request("POST", "/api/home-visits", token=bhw_tok,
                  body={"motherId": mid, "scheduledFor": FUTURE})

        # Schedule a past (overdue) home visit
        c.request("POST", "/api/home-visits", token=bhw_tok,
                  body={"motherId": mid, "scheduledFor": PAST})

        res = c.request("GET", "/api/bhw/dashboard", token=bhw_tok)
        # The past visit should count as overdue
        self.assertGreaterEqual(res.data["homeVisits"]["overdue"], 1)

    def test_bhw_dashboard_counts_pending_referrals_for_assigned_mothers(self):
        db  = open_database(":memory:", shared_password=STAFF_PASSWORD)
        app = create_app(db, log=lambda *_: None)
        c   = Client(app)

        bhw_tok = c.sign_in("b.reyes@bhw.example")
        mid_tok = c.sign_in("c.lim@rhu.example")
        bhw_me  = c.request("GET", "/api/me", token=bhw_tok)
        bhw_uid = bhw_me.data["user"]["id"]

        mother_res = c.request(
            "POST", "/api/mothers",
            token=mid_tok,
            body={"name": "Referral Mother", "consentRecordedAt": CONSENT, "assignedBhwId": bhw_uid},
        )
        mid = mother_res.data["mother"]["id"]

        # Zero referrals to start
        res0 = c.request("GET", "/api/bhw/dashboard", token=bhw_tok)
        start = res0.data["pendingReferrals"]

        # Create one referral
        c.request("POST", "/api/referrals", token=bhw_tok,
                  body={"mother_id": mid, "reason": "Elevated BP"})

        res1 = c.request("GET", "/api/bhw/dashboard", token=bhw_tok)
        self.assertEqual(res1.data["pendingReferrals"], start + 1)

    def test_bhw_dashboard_counts_open_flags_for_assigned_mothers(self):
        db  = open_database(":memory:", shared_password=STAFF_PASSWORD)
        app = create_app(db, log=lambda *_: None)
        c   = Client(app)

        bhw_tok = c.sign_in("b.reyes@bhw.example")
        mid_tok = c.sign_in("c.lim@rhu.example")
        bhw_me  = c.request("GET", "/api/me", token=bhw_tok)
        bhw_uid = bhw_me.data["user"]["id"]

        mother_res = c.request(
            "POST", "/api/mothers",
            token=mid_tok,
            body={"name": "Flag Mother", "consentRecordedAt": CONSENT, "assignedBhwId": bhw_uid},
        )
        mid = mother_res.data["mother"]["id"]

        # Zero flags to start
        res0 = c.request("GET", "/api/bhw/dashboard", token=bhw_tok)
        start = res0.data["openFlags"]

        # Record an observation (creates a flag)
        c.request(
            "POST", f"/api/mothers/{mid}/observations",
            token=bhw_tok,
            body={"observation": "Patient has severe headache"},
        )

        res1 = c.request("GET", "/api/bhw/dashboard", token=bhw_tok)
        self.assertEqual(res1.data["openFlags"], start + 1)

    def test_two_bhws_see_only_their_own_mothers(self):
        """BHW scoping: each BHW sees only the mothers assigned to them."""
        db  = open_database(":memory:", shared_password=STAFF_PASSWORD)
        app = create_app(db, log=lambda *_: None)
        c   = Client(app)

        bhw1_tok = c.sign_in("b.reyes@bhw.example")
        mid_tok  = c.sign_in("c.lim@rhu.example")
        bhw1_me  = c.request("GET", "/api/me", token=bhw1_tok)
        bhw1_uid = bhw1_me.data["user"]["id"]

        # Invite a second BHW
        admin_tok = c.sign_in("admin@example.com")
        c.request("POST", "/api/accounts", token=admin_tok,
                  body={"name": "BHW Two", "email": "bhw2@test.example", "role": "bhw"})
        # Set password via reset flow — simpler: use a known test pattern
        # Register as bhw2 directly in DB for the test
        import users as users_mod
        users_mod.invite_account(db, {"name": "BHW Two Test", "email": "bhw2b@test.example", "role": "bhw"})
        # Sign in by resetting the password
        log_lines = []
        users_mod.request_password_reset(db, "bhw2b@test.example", log_lines.append)
        import re
        code_line = next(l for l in log_lines if "bhw2b@test.example" in l)
        code = re.search(r": ([A-F0-9]{8})", code_line).group(1)
        users_mod.confirm_password_reset(db, {"code": code, "password": STAFF_PASSWORD})
        bhw2_tok = c.sign_in("bhw2b@test.example")
        bhw2_me  = c.request("GET", "/api/me", token=bhw2_tok)
        bhw2_uid = bhw2_me.data["user"]["id"]

        # Assign one mother to BHW1 and one to BHW2
        c.request("POST", "/api/mothers", token=mid_tok,
                  body={"name": "BHW1 Mother", "consentRecordedAt": CONSENT, "assignedBhwId": bhw1_uid})
        c.request("POST", "/api/mothers", token=mid_tok,
                  body={"name": "BHW2 Mother", "consentRecordedAt": CONSENT, "assignedBhwId": bhw2_uid})

        res1 = c.request("GET", "/api/bhw/dashboard", token=bhw1_tok)
        res2 = c.request("GET", "/api/bhw/dashboard", token=bhw2_tok)

        self.assertEqual(res1.data["mothers"]["total"], 1,
                         "BHW1 should only see 1 assigned mother")
        self.assertEqual(res2.data["mothers"]["total"], 1,
                         "BHW2 should only see 1 assigned mother")


# ── RHU dashboard tests ───────────────────────────────────────────────────────

class RhuDashboardTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db     = open_database(":memory:", shared_password=STAFF_PASSWORD)
        cls.app    = create_app(cls.db, log=lambda *_: None)
        cls.client = Client(cls.app)

        cls.tok = {
            "bhw":       cls.client.sign_in("b.reyes@bhw.example"),
            "midwife":   cls.client.sign_in("c.lim@rhu.example"),
            "physician": cls.client.sign_in("a.okafor@rhu.example"),
            "rhu_admin": cls.client.sign_in("rhu.admin@rhu.example"),
            "sysadmin":  cls.client.sign_in("admin@example.com"),
        }

    def req(self, method, path, **kwargs):
        return self.client.request(method, path, **kwargs)

    # ── Access control ────────────────────────────────────────────────────────

    def test_rhu_dashboard_requires_authentication(self):
        res = self.req("GET", "/api/rhu/dashboard")
        self.assertEqual(res.status, 401)

    def test_bhw_cannot_access_rhu_dashboard(self):
        res = self.req("GET", "/api/rhu/dashboard", token=self.tok["bhw"])
        self.assertEqual(res.status, 403)

    def test_sysadmin_cannot_access_rhu_dashboard(self):
        """sysadmin is explicitly excluded — it's an IT role, not a clinical one."""
        res = self.req("GET", "/api/rhu/dashboard", token=self.tok["sysadmin"])
        self.assertEqual(res.status, 403)

    def test_rhu_admin_can_access_rhu_dashboard(self):
        res = self.req("GET", "/api/rhu/dashboard", token=self.tok["rhu_admin"])
        self.assertEqual(res.status, 200)

    def test_physician_can_access_rhu_dashboard(self):
        res = self.req("GET", "/api/rhu/dashboard", token=self.tok["physician"])
        self.assertEqual(res.status, 200)

    def test_midwife_can_access_rhu_dashboard(self):
        res = self.req("GET", "/api/rhu/dashboard", token=self.tok["midwife"])
        self.assertEqual(res.status, 200)

    # ── Response shape ────────────────────────────────────────────────────────

    def test_rhu_dashboard_response_has_expected_keys(self):
        res = self.req("GET", "/api/rhu/dashboard", token=self.tok["rhu_admin"])
        self.assertEqual(res.status, 200)
        for key in (
            "registeredPrenatal",
            "registeredPostpartum",
            "underPrenatalMonitoring",
            "postpartumFollowUp",
            "openFlags",
            "referralsByStatus",
            "overdueReferrals",
        ):
            self.assertIn(key, res.data, f"missing key: {key}")

    def test_rhu_dashboard_referrals_by_status_has_all_statuses(self):
        res = self.req("GET", "/api/rhu/dashboard", token=self.tok["rhu_admin"])
        rbs = res.data["referralsByStatus"]
        for status in ("pending", "acknowledged", "attended", "closed"):
            self.assertIn(status, rbs)

    # ── Correct counts after inserting known data ─────────────────────────────

    def test_rhu_dashboard_counts_registered_prenatal_mothers(self):
        db  = open_database(":memory:", shared_password=STAFF_PASSWORD)
        app = create_app(db, log=lambda *_: None)
        c   = Client(app)

        mid_tok = c.sign_in("c.lim@rhu.example")
        rhu_tok = c.sign_in("rhu.admin@rhu.example")

        res0 = c.request("GET", "/api/rhu/dashboard", token=rhu_tok)
        start = res0.data["registeredPrenatal"]

        # Register 3 prenatal mothers
        for i in range(3):
            c.request("POST", "/api/mothers", token=mid_tok,
                      body={"name": f"Prenatal Mom {i}", "consentRecordedAt": CONSENT})

        res1 = c.request("GET", "/api/rhu/dashboard", token=rhu_tok)
        self.assertEqual(res1.data["registeredPrenatal"], start + 3)

    def test_rhu_dashboard_counts_open_flags(self):
        db  = open_database(":memory:", shared_password=STAFF_PASSWORD)
        app = create_app(db, log=lambda *_: None)
        c   = Client(app)

        bhw_tok = c.sign_in("b.reyes@bhw.example")
        mid_tok = c.sign_in("c.lim@rhu.example")
        rhu_tok = c.sign_in("rhu.admin@rhu.example")

        # Baseline
        res0 = c.request("GET", "/api/rhu/dashboard", token=rhu_tok)
        start_flags = res0.data["openFlags"]

        # Register a mother and raise a flag
        m_res = c.request("POST", "/api/mothers", token=mid_tok,
                          body={"name": "Flag Test Mother", "consentRecordedAt": CONSENT})
        mid = m_res.data["mother"]["id"]
        c.request("POST", f"/api/mothers/{mid}/observations",
                  token=bhw_tok,
                  body={"observation": "Swollen face and hands"})

        res1 = c.request("GET", "/api/rhu/dashboard", token=rhu_tok)
        self.assertEqual(res1.data["openFlags"], start_flags + 1)

    def test_rhu_dashboard_counts_referrals_by_status(self):
        db  = open_database(":memory:", shared_password=STAFF_PASSWORD)
        app = create_app(db, log=lambda *_: None)
        c   = Client(app)

        bhw_tok = c.sign_in("b.reyes@bhw.example")
        mid_tok = c.sign_in("c.lim@rhu.example")
        rhu_tok = c.sign_in("rhu.admin@rhu.example")
        phys_tok= c.sign_in("a.okafor@rhu.example")

        # Baseline
        res0  = c.request("GET", "/api/rhu/dashboard", token=rhu_tok)
        start = res0.data["referralsByStatus"]["pending"]

        # Register a mother and create a referral
        m_res = c.request("POST", "/api/mothers", token=mid_tok,
                          body={"name": "Referral Count Mother", "consentRecordedAt": CONSENT})
        mid = m_res.data["mother"]["id"]
        c.request("POST", "/api/referrals", token=bhw_tok,
                  body={"mother_id": mid, "reason": "Persistent bleeding"})

        res1 = c.request("GET", "/api/rhu/dashboard", token=rhu_tok)
        self.assertEqual(res1.data["referralsByStatus"]["pending"], start + 1)

    def test_rhu_dashboard_counts_overdue_referrals(self):
        """An old pending referral should appear in overdueReferrals."""
        db  = open_database(":memory:", shared_password=STAFF_PASSWORD)
        app = create_app(db, log=lambda *_: None)
        c   = Client(app)

        bhw_tok = c.sign_in("b.reyes@bhw.example")
        mid_tok = c.sign_in("c.lim@rhu.example")
        rhu_tok = c.sign_in("rhu.admin@rhu.example")

        # Baseline
        res0  = c.request("GET", "/api/rhu/dashboard", token=rhu_tok)
        start = res0.data["overdueReferrals"]

        # Register a mother and a referral with a due_by in the past
        m_res = c.request("POST", "/api/mothers", token=mid_tok,
                          body={"name": "Overdue Referral Mother", "consentRecordedAt": CONSENT})
        mid = m_res.data["mother"]["id"]
        c.request("POST", "/api/referrals", token=bhw_tok,
                  body={"mother_id": mid, "reason": "Overdue test", "due_by": PAST})

        res1 = c.request("GET", "/api/rhu/dashboard", token=rhu_tok)
        self.assertEqual(res1.data["overdueReferrals"], start + 1)

    def test_rhu_dashboard_values_are_non_negative_integers(self):
        res = self.req("GET", "/api/rhu/dashboard", token=self.tok["rhu_admin"])
        self.assertEqual(res.status, 200)
        for key in ("registeredPrenatal", "registeredPostpartum",
                    "underPrenatalMonitoring", "postpartumFollowUp",
                    "openFlags", "overdueReferrals"):
            val = res.data[key]
            self.assertIsInstance(val, int, f"{key} should be int, got {type(val)}")
            self.assertGreaterEqual(val, 0, f"{key} should be >= 0")
        for status, val in res.data["referralsByStatus"].items():
            self.assertIsInstance(val, int, f"referralsByStatus.{status} should be int")
            self.assertGreaterEqual(val, 0)


if __name__ == "__main__":
    unittest.main()
