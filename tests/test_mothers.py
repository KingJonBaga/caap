"""Tests for Phase 2: mothers and pregnancy records.

Covers:
  - 401 not signed in
  - 403 wrong role
  - POST /api/mothers happy path
  - 400 missing consent
  - 409 duplicate registration
  - GET /api/mothers (role-scoped visibility)
  - GET /api/mothers/:id (audit row written)
  - POST /api/mothers/:id/history (happy path + validation)
  - GET /api/mothers/:id/history

Run with: py -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAFF_PASSWORD = "test-only-staff-pass"
sys.path.insert(0, str(ROOT / "server"))

from app import create_app   # noqa: E402
from db  import open_database  # noqa: E402
import audit as audit_module  # noqa: E402

import json


# ── Shared helpers ────────────────────────────────────────────────────────────

class Result:
    def __init__(self, status, content_type, body, headers):
        self.status = status
        self.content_type = content_type
        self.headers = headers
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


# ── Fixtures ──────────────────────────────────────────────────────────────────

VALID_MOTHER = {
    "name":             "Ana Dela Cruz",
    "dateOfBirth":      "1995-03-14",
    "addressArea":      "Purok 3",
    "barangayId":       "BGY-001",
    "edd":              "2027-01-20",
    "consentRecordedAt":"2026-10-01T08:00:00Z",
}


class MothersTests(unittest.TestCase):

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

    def req(self, method, path, **kwargs):
        return self.client.request(method, path, **kwargs)

    # ── 401 / 403 guards ─────────────────────────────────────────────────────

    def test_unauthenticated_requests_are_401(self):
        self.assertEqual(self.req("GET",  "/api/mothers").status,    401)
        self.assertEqual(self.req("POST", "/api/mothers").status,    401)
        self.assertEqual(self.req("GET",  "/api/mothers/1").status,  401)

    def test_mother_role_cannot_call_the_mothers_api(self):
        # Register a mother-role account then try to list mothers
        self.req("POST", "/api/register",
                 body={"name": "Test Mother", "email": "tm@example.com",
                       "password": "longenough1"})
        tok = self.client.sign_in("tm@example.com", "longenough1")
        self.assertEqual(self.req("GET",  "/api/mothers",   token=tok).status, 403)
        self.assertEqual(self.req("POST", "/api/mothers",   token=tok).status, 403)

    def test_physician_cannot_post_mothers(self):
        """Physician can read mothers but is not in the POST allowed-roles list (task 2.2)."""
        body = {**VALID_MOTHER, "name": "Role Restricted Test", "dateOfBirth": "1980-01-01",
                "consentRecordedAt": "2026-11-01T08:00:00Z"}
        self.assertEqual(self.req("POST", "/api/mothers", token=self.tok["physician"], body=body).status, 403)

    def test_physician_can_read_mothers(self):
        """Physician is in MOTHERS_ROLES so GET /api/mothers must succeed."""
        self.assertEqual(self.req("GET", "/api/mothers", token=self.tok["physician"]).status, 200)

    # ── POST /api/mothers happy path ─────────────────────────────────────────

    def test_bhw_can_register_a_mother(self):
        res = self.req("POST", "/api/mothers", token=self.tok["bhw"],
                       body=VALID_MOTHER)
        self.assertEqual(res.status, 201, res.data)
        m = res.data["mother"]
        self.assertEqual(m["name"], "Ana Dela Cruz")
        self.assertIn("id", m)
        self.assertEqual(m["status"], "prenatal")

    def test_midwife_can_register_a_mother(self):
        body = {**VALID_MOTHER, "name": "Bea Reyes", "dateOfBirth": "1990-05-20",
                "consentRecordedAt": "2026-10-02T08:00:00Z"}
        res = self.req("POST", "/api/mothers", token=self.tok["midwife"], body=body)
        self.assertEqual(res.status, 201)

    def test_physician_cannot_register_a_mother(self):
        """Physicians may read and document but are not permitted to register mothers (task 2.2)."""
        body = {**VALID_MOTHER, "name": "Carla Gomez", "dateOfBirth": "1992-11-01",
                "consentRecordedAt": "2026-10-03T08:00:00Z"}
        res = self.req("POST", "/api/mothers", token=self.tok["physician"], body=body)
        self.assertEqual(res.status, 403)

    # ── 2.4: consent required ─────────────────────────────────────────────────

    def test_missing_consent_returns_400(self):
        body = {k: v for k, v in VALID_MOTHER.items() if k != "consentRecordedAt"}
        body["name"] = "No Consent"
        res = self.req("POST", "/api/mothers", token=self.tok["bhw"], body=body)
        self.assertEqual(res.status, 400)
        self.assertIn("consent", res.data["error"].lower())

    def test_empty_consent_returns_400(self):
        body = {**VALID_MOTHER, "name": "Empty Consent", "consentRecordedAt": ""}
        res = self.req("POST", "/api/mothers", token=self.tok["bhw"], body=body)
        self.assertEqual(res.status, 400)

    # ── 2.3: duplicate prevention ─────────────────────────────────────────────

    def test_duplicate_name_and_dob_returns_409(self):
        body = {**VALID_MOTHER,
                "name": "Duplicate Dela Cruz",
                "dateOfBirth": "1988-07-04",
                "consentRecordedAt": "2026-10-10T08:00:00Z"}
        first  = self.req("POST", "/api/mothers", token=self.tok["bhw"], body=body)
        second = self.req("POST", "/api/mothers", token=self.tok["bhw"], body=body)
        self.assertEqual(first.status, 201)
        self.assertEqual(second.status, 409)
        self.assertIn("already registered", second.data["error"])

    def test_same_name_different_dob_is_allowed(self):
        body1 = {**VALID_MOTHER, "name": "Maria Lim", "dateOfBirth": "1991-01-01",
                 "consentRecordedAt": "2026-10-11T08:00:00Z"}
        body2 = {**VALID_MOTHER, "name": "Maria Lim", "dateOfBirth": "1993-06-15",
                 "consentRecordedAt": "2026-10-12T08:00:00Z"}
        r1 = self.req("POST", "/api/mothers", token=self.tok["bhw"], body=body1)
        r2 = self.req("POST", "/api/mothers", token=self.tok["bhw"], body=body2)
        self.assertEqual(r1.status, 201)
        self.assertEqual(r2.status, 201, "different DOB should not be a duplicate")

    def test_name_normalisation_catches_duplicates(self):
        """Extra spaces and mixed case in the name should still be detected."""
        body1 = {**VALID_MOTHER, "name": "Rosa  Santos", "dateOfBirth": "1985-03-03",
                 "consentRecordedAt": "2026-10-13T08:00:00Z"}
        body2 = {**VALID_MOTHER, "name": "ROSA SANTOS",  "dateOfBirth": "1985-03-03",
                 "consentRecordedAt": "2026-10-14T08:00:00Z"}
        r1 = self.req("POST", "/api/mothers", token=self.tok["bhw"], body=body1)
        r2 = self.req("POST", "/api/mothers", token=self.tok["bhw"], body=body2)
        self.assertEqual(r1.status, 201)
        self.assertEqual(r2.status, 409, "case/space normalisation should detect the duplicate")

    # ── GET /api/mothers – role-scoped visibility ─────────────────────────────

    def test_rhu_admin_sees_all_mothers(self):
        res = self.req("GET", "/api/mothers", token=self.tok["rhu_admin"])
        self.assertEqual(res.status, 200)
        self.assertIsInstance(res.data["mothers"], list)
        self.assertGreater(len(res.data["mothers"]), 0)

    def test_midwife_sees_all_mothers(self):
        res = self.req("GET", "/api/mothers", token=self.tok["midwife"])
        self.assertEqual(res.status, 200)
        self.assertIsInstance(res.data["mothers"], list)

    def test_bhw_sees_only_assigned_mothers(self):
        # BHW registers a mother — that mother is assigned to the BHW
        body = {**VALID_MOTHER, "name": "BHW Only", "dateOfBirth": "1996-09-09",
                "consentRecordedAt": "2026-10-15T08:00:00Z"}
        created = self.req("POST", "/api/mothers", token=self.tok["bhw"], body=body)
        self.assertEqual(created.status, 201)
        bhw_id = created.data["mother"]["assignedBhwId"]

        bhw_list = self.req("GET", "/api/mothers", token=self.tok["bhw"])
        # Every mother the BHW sees must be assigned to this BHW
        for m in bhw_list.data["mothers"]:
            self.assertEqual(m["assignedBhwId"], bhw_id,
                             f"BHW should only see their own mothers, got assignedBhwId={m['assignedBhwId']}")

    # ── GET /api/mothers/:id ─────────────────────────────────────────────────

    def test_get_mother_by_id_returns_the_record(self):
        body = {**VALID_MOTHER, "name": "Get By ID", "dateOfBirth": "1994-02-28",
                "consentRecordedAt": "2026-10-16T08:00:00Z"}
        created = self.req("POST", "/api/mothers", token=self.tok["midwife"], body=body)
        mid = created.data["mother"]["id"]

        res = self.req("GET", f"/api/mothers/{mid}", token=self.tok["physician"])
        self.assertEqual(res.status, 200)
        self.assertEqual(res.data["mother"]["id"], mid)

    def test_get_unknown_mother_returns_404(self):
        res = self.req("GET", "/api/mothers/99999", token=self.tok["midwife"])
        self.assertEqual(res.status, 404)

    # ── 2.5: audit logging ────────────────────────────────────────────────────

    def test_reading_a_mother_record_writes_an_audit_row(self):
        body = {**VALID_MOTHER, "name": "Audit Test", "dateOfBirth": "1997-07-07",
                "consentRecordedAt": "2026-10-17T08:00:00Z"}
        created = self.req("POST", "/api/mothers", token=self.tok["midwife"], body=body)
        mid = created.data["mother"]["id"]

        # Read the record
        self.req("GET", f"/api/mothers/{mid}", token=self.tok["physician"])

        # Check audit log
        events = audit_module.list_audit(self.db)
        view_events = [e for e in events if e["action"] == "mother_view" and e["detail"] == str(mid)]
        self.assertGreater(len(view_events), 0, "mother_view must be written to audit_log")

    def test_creating_a_mother_writes_an_audit_row(self):
        body = {**VALID_MOTHER, "name": "Audit Create", "dateOfBirth": "1998-08-08",
                "consentRecordedAt": "2026-10-18T08:00:00Z"}
        res = self.req("POST", "/api/mothers", token=self.tok["bhw"], body=body)
        self.assertEqual(res.status, 201)
        mid = str(res.data["mother"]["id"])

        events = audit_module.list_audit(self.db)
        created_events = [e for e in events
                          if e["action"] == "mother_created" and e["detail"] == mid]
        self.assertGreater(len(created_events), 0,
                           "mother_created must be written to audit_log")

    # ── Pregnancy history ─────────────────────────────────────────────────────

    def test_adding_pregnancy_history_succeeds(self):
        body = {**VALID_MOTHER, "name": "History Test", "dateOfBirth": "1993-04-04",
                "consentRecordedAt": "2026-10-19T08:00:00Z"}
        created = self.req("POST", "/api/mothers", token=self.tok["midwife"], body=body)
        mid = created.data["mother"]["id"]

        res = self.req("POST", f"/api/mothers/{mid}/history", token=self.tok["midwife"],
                       body={"gravida": 2, "para": 1, "previousComplications": "None noted"})
        self.assertEqual(res.status, 201)
        h = res.data["history"]
        self.assertEqual(h["gravida"], 2)
        self.assertEqual(h["para"], 1)

    def test_invalid_gravida_returns_400(self):
        body = {**VALID_MOTHER, "name": "Bad Gravida", "dateOfBirth": "1991-05-05",
                "consentRecordedAt": "2026-10-20T08:00:00Z"}
        created = self.req("POST", "/api/mothers", token=self.tok["midwife"], body=body)
        mid = created.data["mother"]["id"]

        res = self.req("POST", f"/api/mothers/{mid}/history", token=self.tok["midwife"],
                       body={"gravida": 0, "para": 0})
        self.assertEqual(res.status, 400)

    def test_listing_pregnancy_history(self):
        body = {**VALID_MOTHER, "name": "List History", "dateOfBirth": "1989-06-06",
                "consentRecordedAt": "2026-10-21T08:00:00Z"}
        created = self.req("POST", "/api/mothers", token=self.tok["midwife"], body=body)
        mid = created.data["mother"]["id"]

        self.req("POST", f"/api/mothers/{mid}/history", token=self.tok["midwife"],
                 body={"gravida": 1, "para": 0})
        self.req("POST", f"/api/mothers/{mid}/history", token=self.tok["midwife"],
                 body={"gravida": 2, "para": 1})

        res = self.req("GET", f"/api/mothers/{mid}/history", token=self.tok["physician"])
        self.assertEqual(res.status, 200)
        self.assertEqual(len(res.data["history"]), 2)

    def test_history_for_unknown_mother_returns_404(self):
        res = self.req("GET", "/api/mothers/99999/history", token=self.tok["midwife"])
        self.assertEqual(res.status, 404)

    # ── Validation edge cases ─────────────────────────────────────────────────

    def test_missing_name_returns_400(self):
        body = {**VALID_MOTHER, "name": "", "consentRecordedAt": "2026-10-22T08:00:00Z"}
        res = self.req("POST", "/api/mothers", token=self.tok["bhw"], body=body)
        self.assertEqual(res.status, 400)

    def test_name_too_long_returns_400(self):
        body = {**VALID_MOTHER, "name": "A" * 151, "consentRecordedAt": "2026-10-23T08:00:00Z"}
        res = self.req("POST", "/api/mothers", token=self.tok["bhw"], body=body)
        self.assertEqual(res.status, 400)


if __name__ == "__main__":
    unittest.main()
