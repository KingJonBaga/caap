"""Tests for Phase 5, task 5.3: observation recording and risk flag matching.

Covers:
  - A BHW or midwife can POST an observation and receive a new risk_flag.
  - If the observation text matches an active indicator, the flag is linked via
    indicator_id.
  - If no indicator matches, a free-text flag is created (indicator_id is NULL).
  - Every new flag starts with status='open'.
  - A flag is never auto-dismissed: the status remains 'open' after the POST.
  - Missing observation text returns 400.
  - A non-BHW/midwife role (physician, rhu_admin) is refused with 403.
  - An unauthenticated request is refused with 401.

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


# ── Minimal request / response helpers (mirror test_api.py) ──────────────────

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


# ── Test class ────────────────────────────────────────────────────────────────

class ObservationTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db = open_database(":memory:", shared_password=STAFF_PASSWORD)
        cls.app = create_app(cls.db, log=lambda *_: None)
        cls.client = Client(cls.app)

        cls.bhw_token      = cls.client.sign_in("b.reyes@bhw.example")
        cls.midwife_token  = cls.client.sign_in("c.lim@rhu.example")
        cls.physician_token = cls.client.sign_in("a.okafor@rhu.example")
        cls.rhu_token      = cls.client.sign_in("rhu.admin@rhu.example")
        cls.admin_token    = cls.client.sign_in("admin@example.com")

        # Create a test mother via the API (BHW registers the mother).
        res = cls.client.request(
            "POST", "/api/mothers",
            body={
                "name": "Ana Cruz",
                "address_area": "Barangay Demo",
                "edd": "2026-12-01",
                "status": "prenatal",
                "consent_recorded_at": "2026-01-01T00:00:00Z",
            },
            token=cls.bhw_token,
        )
        assert res.status == 201, f"mother creation failed: {res.data}"
        cls.mother_id = res.data["mother"]["id"]

        # Create an active indicator via admin.
        res = cls.client.request(
            "POST", "/api/indicators",
            body={
                "code": "HIGH_BP",
                "description": "elevated blood pressure",
                "source_reference": "WHO Maternal Guide §4.2",
                "approved_by": "Dr. Santos",
                "approved_at": "2026-01-01",
            },
            token=cls.admin_token,
        )
        assert res.status == 201, f"indicator creation failed: {res.data}"
        cls.indicator_id = res.data["indicator"]["id"]

    _NO_TOKEN = object()  # sentinel to distinguish "no token" from default

    def _post_obs(self, text, token=_NO_TOKEN):
        tok = self.bhw_token if token is ObservationTests._NO_TOKEN else token
        return self.client.request(
            "POST", f"/api/mothers/{self.mother_id}/observations",
            body={"observation": text},
            token=tok,
        )

    # ── Happy path: BHW ──────────────────────────────────────────────────────

    def test_bhw_can_record_observation(self):
        res = self._post_obs("Patient reports dizziness", token=self.bhw_token)
        self.assertEqual(res.status, 201, res.data)
        self.assertIn("flag", res.data)

    def test_midwife_can_record_observation(self):
        res = self._post_obs("Patient has oedema", token=self.midwife_token)
        self.assertEqual(res.status, 201, res.data)
        self.assertIn("flag", res.data)

    # ── Flag matches active indicator ─────────────────────────────────────────

    def test_observation_matching_indicator_links_indicator_id(self):
        """Observation text that contains the indicator code (HIGH_BP) → linked flag."""
        res = self._post_obs("Observed HIGH_BP reading of 160/110")
        self.assertEqual(res.status, 201, res.data)
        flag = res.data["flag"]
        self.assertEqual(flag["indicator_id"], self.indicator_id,
                         "indicator_id should reference the matched indicator")

    def test_observation_matching_indicator_description_links_indicator_id(self):
        """Match on the description text 'elevated blood pressure'."""
        res = self._post_obs("Mother shows signs of elevated blood pressure today")
        self.assertEqual(res.status, 201, res.data)
        flag = res.data["flag"]
        self.assertEqual(flag["indicator_id"], self.indicator_id)

    # ── Free-text flag (no indicator match) ──────────────────────────────────

    def test_observation_with_no_matching_indicator_creates_free_text_flag(self):
        res = self._post_obs("Mother reports nausea and fatigue")
        self.assertEqual(res.status, 201, res.data)
        flag = res.data["flag"]
        self.assertIsNone(flag["indicator_id"],
                          "indicator_id should be None when no indicator matches")

    # ── Flag is never auto-dismissed ──────────────────────────────────────────

    def test_new_flag_status_is_open(self):
        """Every newly created flag must have status='open' — never auto-dismissed."""
        res = self._post_obs("Unusual oedema noticed")
        self.assertEqual(res.status, 201, res.data)
        self.assertEqual(res.data["flag"]["status"], "open",
                         "Flag must start as 'open', never auto-dismissed")

    def test_matched_flag_status_is_open(self):
        """Even when an indicator matches, the flag must be 'open', not dismissed."""
        res = self._post_obs("HIGH_BP confirmed at checkup")
        self.assertEqual(res.status, 201, res.data)
        self.assertEqual(res.data["flag"]["status"], "open")

    def test_free_text_flag_status_is_open(self):
        """Free-text flags (no indicator match) must also start as 'open'."""
        res = self._post_obs("Mother missed last meal, looks pale")
        self.assertEqual(res.status, 201, res.data)
        self.assertEqual(res.data["flag"]["status"], "open")

    # ── Flag observation text is preserved ───────────────────────────────────

    def test_observation_text_is_stored_on_flag(self):
        obs = "Visible leg swelling, bilateral"
        res = self._post_obs(obs)
        self.assertEqual(res.status, 201, res.data)
        self.assertEqual(res.data["flag"]["observation"], obs)

    # ── Validation ───────────────────────────────────────────────────────────

    def test_missing_observation_returns_400(self):
        res = self.client.request(
            "POST", f"/api/mothers/{self.mother_id}/observations",
            body={},
            token=self.bhw_token,
        )
        self.assertEqual(res.status, 400)

    def test_empty_observation_returns_400(self):
        res = self._post_obs("   ")
        self.assertEqual(res.status, 400)

    def test_unknown_mother_returns_404(self):
        res = self.client.request(
            "POST", "/api/mothers/999999/observations",
            body={"observation": "test note"},
            token=self.bhw_token,
        )
        self.assertEqual(res.status, 404)

    # ── Role checks ──────────────────────────────────────────────────────────

    def test_unauthenticated_request_returns_401(self):
        res = self._post_obs("note", token=None)
        self.assertEqual(res.status, 401)

    def test_physician_cannot_record_observation_returns_403(self):
        res = self._post_obs("note", token=self.physician_token)
        self.assertEqual(res.status, 403)

    def test_rhu_admin_cannot_record_observation_returns_403(self):
        res = self._post_obs("note", token=self.rhu_token)
        self.assertEqual(res.status, 403)

    # ── List flags ───────────────────────────────────────────────────────────

    def test_flags_list_returns_recorded_flags(self):
        """GET /api/mothers/:id/flags should return at least the flags we created."""
        res = self.client.request(
            "GET", f"/api/mothers/{self.mother_id}/flags",
            token=self.bhw_token,
        )
        self.assertEqual(res.status, 200, res.data)
        self.assertIn("flags", res.data)
        self.assertIsInstance(res.data["flags"], list)
        self.assertGreater(len(res.data["flags"]), 0)

    def test_flags_list_all_have_status_open(self):
        """All flags we created in this test class start as open."""
        res = self.client.request(
            "GET", f"/api/mothers/{self.mother_id}/flags",
            token=self.bhw_token,
        )
        self.assertEqual(res.status, 200)
        for flag in res.data["flags"]:
            self.assertEqual(flag["status"], "open",
                             f"Flag {flag['id']} should be open, not auto-dismissed")


if __name__ == "__main__":
    unittest.main()
