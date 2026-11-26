"""Tests for Phase 4, task 4.1: checklists table and endpoints.

Covers:
  - POST /api/checklists happy path (creates checklist)
  - POST /api/checklists validation (missing title, bad stage)
  - GET /api/checklists/:id returns an approved checklist
  - GET /api/checklists/:id returns 403 for an unapproved checklist
  - GET /api/checklists/:id returns 404 for an unknown id
  - 401 when not signed in
  - 403 when the wrong role tries to create or fetch a checklist

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
from db  import open_database # noqa: E402


# ── Shared helpers (same pattern used across the test suite) ──────────────────

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
            raw = json.dumps(body).encode("utf-8")
            headers["content-type"] = "application/json"
        if token:
            headers["authorization"] = "Bearer " + token
        return Result(*self.app.handle(method, path, headers, raw))

    def sign_in(self, email, password):
        res = self.request("POST", "/api/login",
                           body={"email": email, "password": password})
        assert res.status == 200, f"login failed for {email}: {res.data}"
        return res.data["token"]


# ── Fixtures ──────────────────────────────────────────────────────────────────

APPROVED_CHECKLIST = {
    "title":      "Prenatal Home Visit Assessment",
    "stage":      "prenatal",
    "version":    "1",
    "approvedBy": "Dr. Ana Reyes",
    "approvedAt": "2026-01-10T08:00:00Z",
    "items": [
        {"question": "Blood pressure measured?", "type": "yesno"},
        {"question": "Weight recorded?",          "type": "yesno"},
    ],
}

UNAPPROVED_CHECKLIST = {
    "title":   "Draft Postpartum Checklist",
    "stage":   "postpartum",
    "version": "0",
    # approvedBy intentionally omitted
    "items": [{"question": "Fundal height checked?", "type": "yesno"}],
}


class ChecklistTests(unittest.TestCase):

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

    # ── 401: no session ───────────────────────────────────────────────────────

    def test_create_checklist_requires_authentication(self):
        res = self.req("POST", "/api/checklists", body=APPROVED_CHECKLIST)
        self.assertEqual(res.status, 401)

    def test_get_checklist_requires_authentication(self):
        res = self.req("GET", "/api/checklists/1")
        self.assertEqual(res.status, 401)

    # ── 403: wrong role for create ────────────────────────────────────────────

    def test_bhw_cannot_create_a_checklist(self):
        res = self.req("POST", "/api/checklists",
                       token=self.tok["bhw"], body=APPROVED_CHECKLIST)
        self.assertEqual(res.status, 403)

    def test_midwife_cannot_create_a_checklist(self):
        res = self.req("POST", "/api/checklists",
                       token=self.tok["midwife"], body=APPROVED_CHECKLIST)
        self.assertEqual(res.status, 403)

    def test_physician_cannot_create_a_checklist(self):
        res = self.req("POST", "/api/checklists",
                       token=self.tok["physician"], body=APPROVED_CHECKLIST)
        self.assertEqual(res.status, 403)

    # ── POST /api/checklists happy path ───────────────────────────────────────

    def test_rhu_admin_can_create_an_approved_checklist(self):
        res = self.req("POST", "/api/checklists",
                       token=self.tok["rhu_admin"], body=APPROVED_CHECKLIST)
        self.assertEqual(res.status, 201, res.data)
        cl = res.data["checklist"]
        self.assertIn("id", cl)
        self.assertEqual(cl["title"],      APPROVED_CHECKLIST["title"])
        self.assertEqual(cl["stage"],      "prenatal")
        self.assertEqual(cl["approvedBy"], "Dr. Ana Reyes")
        self.assertIsInstance(cl["items"], list)
        self.assertEqual(len(cl["items"]), 2)

    def test_sysadmin_can_create_an_unapproved_checklist(self):
        res = self.req("POST", "/api/checklists",
                       token=self.tok["sysadmin"], body=UNAPPROVED_CHECKLIST)
        self.assertEqual(res.status, 201, res.data)
        self.assertIsNone(res.data["checklist"]["approvedBy"])

    # ── POST /api/checklists validation ───────────────────────────────────────

    def test_missing_title_returns_400(self):
        body = {**APPROVED_CHECKLIST, "title": ""}
        res = self.req("POST", "/api/checklists",
                       token=self.tok["rhu_admin"], body=body)
        self.assertEqual(res.status, 400)
        self.assertIn("title", res.data["error"].lower())

    def test_invalid_stage_returns_400(self):
        body = {**APPROVED_CHECKLIST, "stage": "neonatal"}
        res = self.req("POST", "/api/checklists",
                       token=self.tok["rhu_admin"], body=body)
        self.assertEqual(res.status, 400)
        self.assertIn("stage", res.data["error"].lower())

    # ── GET /api/checklists/:id — approved checklist ──────────────────────────

    def test_approved_checklist_is_returned_to_authorised_roles(self):
        # Create the checklist first
        created = self.req("POST", "/api/checklists",
                           token=self.tok["rhu_admin"],
                           body={
                               "title":      "Full Prenatal Check",
                               "stage":      "prenatal",
                               "approvedBy": "Dr. Ben Cruz",
                               "approvedAt": "2026-02-01T08:00:00Z",
                               "items":      [{"question": "Weight?", "type": "yesno"}],
                           })
        self.assertEqual(created.status, 201)
        checklist_id = created.data["checklist"]["id"]

        for role in ("bhw", "midwife", "physician", "rhu_admin"):
            res = self.req("GET", f"/api/checklists/{checklist_id}",
                           token=self.tok[role])
            self.assertEqual(res.status, 200,
                             f"role '{role}' should be able to fetch an approved checklist")
            self.assertEqual(res.data["checklist"]["id"], checklist_id)

    # ── GET /api/checklists/:id — unapproved checklist returns 403 ───────────

    def test_unapproved_checklist_returns_403(self):
        # Create a checklist without approvedBy
        created = self.req("POST", "/api/checklists",
                           token=self.tok["sysadmin"],
                           body={
                               "title": "Draft Only Checklist",
                               "stage": "postpartum",
                               "items": [],
                           })
        self.assertEqual(created.status, 201)
        checklist_id = created.data["checklist"]["id"]

        # Every read role must receive 403 for an unapproved checklist
        for role in ("bhw", "midwife", "physician", "rhu_admin"):
            res = self.req("GET", f"/api/checklists/{checklist_id}",
                           token=self.tok[role])
            self.assertEqual(res.status, 403,
                             f"role '{role}' should get 403 for an unapproved checklist")
            self.assertIn("approved", res.data["error"].lower())

    # ── GET /api/checklists/:id — unknown id returns 404 ─────────────────────

    def test_unknown_checklist_id_returns_404(self):
        res = self.req("GET", "/api/checklists/99999",
                       token=self.tok["bhw"])
        self.assertEqual(res.status, 404)

    # ── checklist items round-trip ────────────────────────────────────────────

    def test_items_are_stored_and_returned_as_a_list(self):
        items = [
            {"question": "Is BP normal?",  "type": "yesno"},
            {"question": "Weight (kg):",   "type": "number"},
            {"question": "Any complaints?","type": "text"},
        ]
        created = self.req("POST", "/api/checklists",
                           token=self.tok["rhu_admin"],
                           body={
                               "title":      "Items Round-Trip Test",
                               "stage":      "prenatal",
                               "approvedBy": "Dr. Liza Go",
                               "items":      items,
                           })
        self.assertEqual(created.status, 201)
        checklist_id = created.data["checklist"]["id"]

        fetched = self.req("GET", f"/api/checklists/{checklist_id}",
                           token=self.tok["bhw"])
        self.assertEqual(fetched.status, 200)
        self.assertEqual(fetched.data["checklist"]["items"], items)


if __name__ == "__main__":
    unittest.main()
