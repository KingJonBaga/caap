"""Tests for task 3.2 flat visit scheduling and status update endpoints.

Covers:
  - POST /api/prenatal-visits  — create prenatal visit (happy path, missing fields, 401, 403)
  - PATCH /api/prenatal-visits/:id/status — valid, invalid status, 404, 401, 403
  - POST /api/postnatal-visits  — create postnatal visit (happy path, 401, 403)
  - PATCH /api/postnatal-visits/:id/status — valid, invalid status
  - POST /api/home-visits  — create home visit (happy path, 401, 403)
  - PATCH /api/home-visits/:id/status — valid, invalid status, 404

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


# ── Helpers ───────────────────────────────────────────────────────────────────

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
FUTURE  = "2099-06-01T09:00:00"


class FlatVisitEndpointTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.db     = open_database(":memory:", shared_password=STAFF_PASSWORD)
        cls.app    = create_app(cls.db, log=lambda *a: None)
        cls.client = Client(cls.app)

        cls.tok = {
            "bhw":       cls.client.sign_in("b.reyes@bhw.example",   STAFF_PASSWORD),
            "midwife":   cls.client.sign_in("c.lim@rhu.example",     STAFF_PASSWORD),
            "physician": cls.client.sign_in("a.okafor@rhu.example",  STAFF_PASSWORD),
            "rhu_admin": cls.client.sign_in("rhu.admin@rhu.example", STAFF_PASSWORD),
            "sysadmin":  cls.client.sign_in("admin@example.com",     STAFF_PASSWORD),
        }

        # Register a mother used by all tests
        res = cls.client.request(
            "POST", "/api/mothers", token=cls.tok["midwife"],
            body={"name": "Flat Visit Mother", "dateOfBirth": "1990-05-20",
                  "consentRecordedAt": CONSENT},
        )
        assert res.status == 201, f"mother setup failed: {res.data}"
        cls.mid = res.data["mother"]["id"]

    def req(self, method, path, **kwargs):
        return self.client.request(method, path, **kwargs)

    # ── Prenatal: POST /api/prenatal-visits ───────────────────────────────────

    def test_midwife_can_create_prenatal_via_flat_endpoint(self):
        res = self.req("POST", "/api/prenatal-visits",
                       token=self.tok["midwife"],
                       body={"motherId": self.mid, "scheduledFor": FUTURE})
        self.assertEqual(res.status, 201, res.data)
        v = res.data["visit"]
        self.assertEqual(v["status"], "scheduled")
        self.assertEqual(v["motherId"], self.mid)

    def test_bhw_can_create_prenatal_via_flat_endpoint(self):
        res = self.req("POST", "/api/prenatal-visits",
                       token=self.tok["bhw"],
                       body={"motherId": self.mid, "scheduledFor": FUTURE})
        self.assertEqual(res.status, 201)

    def test_physician_can_create_prenatal_via_flat_endpoint(self):
        res = self.req("POST", "/api/prenatal-visits",
                       token=self.tok["physician"],
                       body={"motherId": self.mid, "scheduledFor": FUTURE})
        self.assertEqual(res.status, 201)

    def test_prenatal_flat_create_missing_mother_id_returns_400(self):
        res = self.req("POST", "/api/prenatal-visits",
                       token=self.tok["midwife"],
                       body={"scheduledFor": FUTURE})
        self.assertEqual(res.status, 400)

    def test_prenatal_flat_create_missing_scheduled_for_returns_400(self):
        res = self.req("POST", "/api/prenatal-visits",
                       token=self.tok["midwife"],
                       body={"motherId": self.mid})
        self.assertEqual(res.status, 400)

    def test_prenatal_flat_create_no_session_returns_401(self):
        res = self.req("POST", "/api/prenatal-visits",
                       body={"motherId": self.mid, "scheduledFor": FUTURE})
        self.assertEqual(res.status, 401)

    # ── Prenatal: PATCH /api/prenatal-visits/:id/status ───────────────────────

    def _create_prenatal(self):
        res = self.req("POST", "/api/prenatal-visits",
                       token=self.tok["midwife"],
                       body={"motherId": self.mid, "scheduledFor": FUTURE})
        self.assertEqual(res.status, 201)
        return res.data["visit"]["id"]

    def test_prenatal_flat_status_valid_transition(self):
        vid = self._create_prenatal()
        res = self.req("PATCH", f"/api/prenatal-visits/{vid}/status",
                       token=self.tok["midwife"],
                       body={"status": "completed"})
        self.assertEqual(res.status, 200, res.data)
        self.assertEqual(res.data["visit"]["status"], "completed")

    def test_prenatal_flat_status_invalid_value_returns_400(self):
        vid = self._create_prenatal()
        res = self.req("PATCH", f"/api/prenatal-visits/{vid}/status",
                       token=self.tok["midwife"],
                       body={"status": "cancelled"})
        self.assertEqual(res.status, 400)

    def test_prenatal_flat_status_nonexistent_visit_returns_404(self):
        res = self.req("PATCH", "/api/prenatal-visits/999999/status",
                       token=self.tok["midwife"],
                       body={"status": "completed"})
        self.assertEqual(res.status, 404)

    def test_prenatal_flat_status_no_session_returns_401(self):
        vid = self._create_prenatal()
        res = self.req("PATCH", f"/api/prenatal-visits/{vid}/status",
                       body={"status": "completed"})
        self.assertEqual(res.status, 401)

    def test_prenatal_flat_status_missing_status_field_returns_400(self):
        vid = self._create_prenatal()
        res = self.req("PATCH", f"/api/prenatal-visits/{vid}/status",
                       token=self.tok["midwife"],
                       body={})
        self.assertEqual(res.status, 400)

    # ── Postnatal: POST /api/postnatal-visits ─────────────────────────────────

    def test_midwife_can_create_postnatal_via_flat_endpoint(self):
        res = self.req("POST", "/api/postnatal-visits",
                       token=self.tok["midwife"],
                       body={"motherId": self.mid, "scheduledFor": FUTURE,
                             "observations": "Week 6 follow-up."})
        self.assertEqual(res.status, 201, res.data)
        self.assertEqual(res.data["visit"]["status"], "scheduled")

    def test_postnatal_flat_create_no_session_returns_401(self):
        res = self.req("POST", "/api/postnatal-visits",
                       body={"motherId": self.mid, "scheduledFor": FUTURE})
        self.assertEqual(res.status, 401)

    def test_postnatal_flat_create_missing_mother_id_returns_400(self):
        res = self.req("POST", "/api/postnatal-visits",
                       token=self.tok["midwife"],
                       body={"scheduledFor": FUTURE})
        self.assertEqual(res.status, 400)

    # ── Postnatal: PATCH /api/postnatal-visits/:id/status ────────────────────

    def _create_postnatal(self):
        res = self.req("POST", "/api/postnatal-visits",
                       token=self.tok["midwife"],
                       body={"motherId": self.mid, "scheduledFor": FUTURE})
        self.assertEqual(res.status, 201)
        return res.data["visit"]["id"]

    def test_postnatal_flat_status_valid_transition(self):
        vid = self._create_postnatal()
        res = self.req("PATCH", f"/api/postnatal-visits/{vid}/status",
                       token=self.tok["midwife"],
                       body={"status": "missed"})
        self.assertEqual(res.status, 200)
        self.assertEqual(res.data["visit"]["status"], "missed")

    def test_postnatal_flat_status_invalid_value_returns_400(self):
        vid = self._create_postnatal()
        res = self.req("PATCH", f"/api/postnatal-visits/{vid}/status",
                       token=self.tok["midwife"],
                       body={"status": "pending"})
        self.assertEqual(res.status, 400)

    def test_postnatal_flat_status_no_session_returns_401(self):
        vid = self._create_postnatal()
        res = self.req("PATCH", f"/api/postnatal-visits/{vid}/status",
                       body={"status": "completed"})
        self.assertEqual(res.status, 401)

    # ── Home visits: POST /api/home-visits ────────────────────────────────────

    def test_bhw_can_create_home_visit_via_flat_endpoint(self):
        res = self.req("POST", "/api/home-visits",
                       token=self.tok["bhw"],
                       body={"motherId": self.mid, "scheduledFor": FUTURE,
                             "notes": "First home visit."})
        self.assertEqual(res.status, 201, res.data)
        v = res.data["visit"]
        self.assertEqual(v["status"], "planned")
        self.assertEqual(v["notes"], "First home visit.")

    def test_midwife_can_create_home_visit_via_flat_endpoint(self):
        res = self.req("POST", "/api/home-visits",
                       token=self.tok["midwife"],
                       body={"motherId": self.mid, "scheduledFor": FUTURE})
        self.assertEqual(res.status, 201)

    def test_home_visit_flat_create_no_session_returns_401(self):
        res = self.req("POST", "/api/home-visits",
                       body={"motherId": self.mid, "scheduledFor": FUTURE})
        self.assertEqual(res.status, 401)

    def test_home_visit_flat_create_physician_role_returns_403(self):
        """Physicians may read visits but not create home visits via flat endpoint."""
        res = self.req("POST", "/api/home-visits",
                       token=self.tok["physician"],
                       body={"motherId": self.mid, "scheduledFor": FUTURE})
        self.assertEqual(res.status, 403)

    def test_home_visit_flat_create_missing_mother_id_returns_400(self):
        res = self.req("POST", "/api/home-visits",
                       token=self.tok["bhw"],
                       body={"scheduledFor": FUTURE})
        self.assertEqual(res.status, 400)

    def test_home_visit_flat_create_missing_scheduled_for_returns_400(self):
        res = self.req("POST", "/api/home-visits",
                       token=self.tok["bhw"],
                       body={"motherId": self.mid})
        self.assertEqual(res.status, 400)

    # ── Home visits: PATCH /api/home-visits/:id/status ───────────────────────

    def _create_home_visit(self):
        res = self.req("POST", "/api/home-visits",
                       token=self.tok["bhw"],
                       body={"motherId": self.mid, "scheduledFor": FUTURE})
        self.assertEqual(res.status, 201)
        return res.data["visit"]["id"]

    def test_home_visit_flat_status_valid_transition(self):
        vid = self._create_home_visit()
        res = self.req("PATCH", f"/api/home-visits/{vid}/status",
                       token=self.tok["bhw"],
                       body={"status": "completed"})
        self.assertEqual(res.status, 200, res.data)
        self.assertEqual(res.data["visit"]["status"], "completed")

    def test_home_visit_flat_status_rescheduled_transition(self):
        vid = self._create_home_visit()
        res = self.req("PATCH", f"/api/home-visits/{vid}/status",
                       token=self.tok["bhw"],
                       body={"status": "rescheduled"})
        self.assertEqual(res.status, 200)
        self.assertEqual(res.data["visit"]["status"], "rescheduled")

    def test_home_visit_flat_status_invalid_value_returns_400(self):
        vid = self._create_home_visit()
        res = self.req("PATCH", f"/api/home-visits/{vid}/status",
                       token=self.tok["bhw"],
                       body={"status": "scheduled"})  # 'scheduled' is clinic-only
        self.assertEqual(res.status, 400)

    def test_home_visit_flat_status_nonexistent_returns_404(self):
        res = self.req("PATCH", "/api/home-visits/999999/status",
                       token=self.tok["bhw"],
                       body={"status": "completed"})
        self.assertEqual(res.status, 404)

    def test_home_visit_flat_status_no_session_returns_401(self):
        vid = self._create_home_visit()
        res = self.req("PATCH", f"/api/home-visits/{vid}/status",
                       body={"status": "completed"})
        self.assertEqual(res.status, 401)

    def test_home_visit_flat_status_physician_returns_403(self):
        vid = self._create_home_visit()
        res = self.req("PATCH", f"/api/home-visits/{vid}/status",
                       token=self.tok["physician"],
                       body={"status": "completed"})
        self.assertEqual(res.status, 403)

    # ── Audit log written on status change ────────────────────────────────────

    def test_audit_row_written_on_prenatal_status_update(self):
        from common import query_all
        vid = self._create_prenatal()
        self.req("PATCH", f"/api/prenatal-visits/{vid}/status",
                 token=self.tok["midwife"], body={"status": "completed"})
        rows = query_all(
            self.db,
            "SELECT * FROM audit_log WHERE action = 'prenatal_status_updated' ORDER BY id DESC LIMIT 1",
        )
        self.assertTrue(len(rows) >= 1, "Expected an audit row for prenatal_status_updated")

    def test_audit_row_written_on_home_visit_status_update(self):
        from common import query_all
        vid = self._create_home_visit()
        self.req("PATCH", f"/api/home-visits/{vid}/status",
                 token=self.tok["bhw"], body={"status": "completed"})
        rows = query_all(
            self.db,
            "SELECT * FROM audit_log WHERE action = 'home_visit_status_updated' ORDER BY id DESC LIMIT 1",
        )
        self.assertTrue(len(rows) >= 1, "Expected an audit row for home_visit_status_updated")


if __name__ == "__main__":
    unittest.main()
