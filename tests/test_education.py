"""Phase 10: education materials. Mothers see only approved items for their stage.

Uses an in-memory database, so data/caap.db is never touched."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

from app import create_app  # noqa: E402
from db import open_database  # noqa: E402

PASSWORD = "test-only-education-pass"


class EducationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = open_database(":memory:", shared_password=PASSWORD)
        cls.app = create_app(cls.db, log=lambda *_: None)
        cls.mother = cls.sign_in("maria.santos@example.com")
        cls.physician = cls.sign_in("a.okafor@rhu.example")
        cls.bhw = cls.sign_in("b.reyes@bhw.example")
        cls.rhu = cls.sign_in("rhu.admin@rhu.example")
        cls.sysadmin = cls.sign_in("admin@example.com")

    @classmethod
    def sign_in(cls, email):
        headers = {"content-type": "application/json"}
        status, _, data, _ = cls.app.handle(
            "POST", "/api/login", headers, json.dumps({"email": email, "password": PASSWORD}).encode("utf-8")
        )
        assert status == 200, email
        return json.loads(data)["token"]

    def call(self, method, path, token=None, body=None):
        headers = {"content-type": "application/json"} if body is not None else {}
        if token:
            headers["authorization"] = "Bearer " + token
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        status, content_type, data, _ = self.app.handle(method, path, headers, raw)
        payload = json.loads(data) if content_type and content_type.startswith("application/json") else data
        return status, payload

    def make_item(self, title, stage, approved_by="Dr. A. Okafor"):
        body = {"title": title, "stage": stage, "body": "Plain-language text for mothers."}
        if approved_by is not None:
            body["approvedBy"] = approved_by
        return self.call("POST", "/api/education", token=self.physician, body=body)

    # ── 10.1: only approved items reach mothers ─────────────────

    def test_unapproved_item_is_hidden_from_mothers(self):
        status, _ = self.make_item("Draft leaflet", "prenatal", approved_by=None)
        self.assertEqual(status, 400, "an item must name its approver")

        # The API refuses to save an item with no approver, so an unapproved item
        # can only exist if it was stored without one. Insert one directly to test the filter.
        self.db.execute(
            "INSERT INTO education_items (title, stage, body, approved_by, approved_at) VALUES (?, ?, ?, NULL, NULL)",
            ("Unapproved draft", "prenatal", "Not yet reviewed."),
        )
        _, mother_view = self.call("GET", "/api/education", token=self.mother)
        titles = [item["title"] for item in mother_view["items"]]
        self.assertNotIn("Unapproved draft", titles)

        _, staff_view = self.call("GET", "/api/education", token=self.physician)
        draft = next(item for item in staff_view["items"] if item["title"] == "Unapproved draft")
        self.assertFalse(draft["approved"])

    def test_approved_item_is_visible_to_mothers_with_approver_shown(self):
        status, created = self.make_item("Eating well in pregnancy", "prenatal")
        self.assertEqual(status, 201)
        self.assertEqual(created["item"]["approvedBy"], "Dr. A. Okafor")

        _, mother_view = self.call("GET", "/api/education", token=self.mother)
        match = next(item for item in mother_view["items"] if item["title"] == "Eating well in pregnancy")
        self.assertTrue(match["approved"])

    # ── 10.2: only the mother's stage ───────────────────────────

    def test_mother_only_sees_items_for_the_requested_stage(self):
        self.make_item("Stage check prenatal", "prenatal")
        self.make_item("Stage check postpartum", "postpartum")

        _, prenatal = self.call("GET", "/api/education?stage=prenatal", token=self.mother)
        titles = [item["title"] for item in prenatal["items"]]
        self.assertIn("Stage check prenatal", titles)
        self.assertNotIn("Stage check postpartum", titles)

        _, postpartum = self.call("GET", "/api/education?stage=postpartum", token=self.mother)
        titles = [item["title"] for item in postpartum["items"]]
        self.assertIn("Stage check postpartum", titles)
        self.assertNotIn("Stage check prenatal", titles)

    def test_invalid_stage_is_refused_when_creating(self):
        status, _ = self.call("POST", "/api/education", token=self.physician,
                              body={"title": "X", "stage": "toddler", "body": "Y", "approvedBy": "Dr"})
        self.assertEqual(status, 400)

    # ── Roles ───────────────────────────────────────────────────

    def test_only_physicians_and_rhu_admins_can_create(self):
        self.assertEqual(self.call("POST", "/api/education", token=self.bhw,
                                   body={"title": "T", "stage": "prenatal", "body": "B", "approvedBy": "Dr"})[0], 403)
        self.assertEqual(self.call("POST", "/api/education", token=self.mother,
                                   body={"title": "T", "stage": "prenatal", "body": "B", "approvedBy": "Dr"})[0], 403)
        self.assertEqual(self.call("POST", "/api/education",
                                   body={"title": "T", "stage": "prenatal", "body": "B", "approvedBy": "Dr"})[0], 401)

    def test_rhu_admin_can_create_too(self):
        status, _ = self.call("POST", "/api/education", token=self.rhu,
                              body={"title": "RHU approved", "stage": "postpartum", "body": "B", "approvedBy": "RHU"})
        self.assertEqual(status, 201)

    def test_staff_can_read_everything_and_see_approval_status(self):
        self.db.execute(
            "INSERT INTO education_items (title, stage, body, approved_by, approved_at) VALUES (?, ?, ?, NULL, NULL)",
            ("Staff-only draft", "postpartum", "Pending."),
        )
        status, data = self.call("GET", "/api/education", token=self.bhw)
        self.assertEqual(status, 200)
        self.assertIn("Staff-only draft", [item["title"] for item in data["items"]])


if __name__ == "__main__":
    unittest.main()
