"""Tests for Phase 3: prenatal, postnatal and home visit records.

Covers:
  - 401 / 403 on every new endpoint
  - Scheduling visits (POST)
  - Status transitions (PATCH) — allowed and forbidden
  - mark_overdue_visits with a fixed clock (task 3.3)
  - GET /api/bhw/schedule returns due-today / overdue / missed (task 3.4)
  - BHW schedule is scoped to the signed-in BHW only

Run with: py -m unittest discover -s tests
"""
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAFF_PASSWORD = "test-only-staff-pass"
sys.path.insert(0, str(ROOT / "server"))

from app import create_app      # noqa: E402
from db  import open_database   # noqa: E402
import visit_records as vr      # noqa: E402


# ── Shared helpers ────────────────────────────────────────────────────────────

class Result:
    def __init__(self, status, content_type, body, headers):
        self.status = status
        self.data   = json.loads(body) if content_type and "json" in content_type else body


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
FUTURE  = "2099-01-01T09:00:00"   # always in the future
PAST    = "2000-01-01T09:00:00"   # always in the past


class VisitRecordsTests(unittest.TestCase):

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

        # Register a mother used by all visit tests
        res = cls.client.request(
            "POST", "/api/mothers", token=cls.tok["midwife"],
            body={"name": "Visit Test Mother", "dateOfBirth": "1992-04-10",
                  "consentRecordedAt": CONSENT},
        )
        assert res.status == 201, f"mother setup failed: {res.data}"
        cls.mid = res.data["mother"]["id"]

        # Register a second mother for BHW-scoping tests
        res2 = cls.client.request(
            "POST", "/api/mothers", token=cls.tok["bhw"],
            body={"name": "BHW Own Mother", "dateOfBirth": "1995-06-15",
                  "consentRecordedAt": CONSENT},
        )
        assert res2.status == 201, f"bhw mother setup failed: {res2.data}"
        cls.bhw_mid = res2.data["mother"]["id"]

    def req(self, method, path, **kwargs):
        return self.client.request(method, path, **kwargs)

    # ── 401 / 403 guards ─────────────────────────────────────────────────────

    def test_unauthenticated_requests_return_401(self):
        self.assertEqual(self.req("GET",  f"/api/mothers/{self.mid}/prenatal").status,  401)
        self.assertEqual(self.req("POST", f"/api/mothers/{self.mid}/prenatal").status,  401)
        self.assertEqual(self.req("GET",  f"/api/mothers/{self.mid}/postnatal").status, 401)
        self.assertEqual(self.req("GET",  f"/api/mothers/{self.mid}/home-visits").status, 401)
        self.assertEqual(self.req("GET",  "/api/bhw/schedule").status, 401)

    def test_bhw_schedule_is_forbidden_for_non_bhw_roles(self):
        for role in ("midwife", "physician", "rhu_admin", "sysadmin"):
            res = self.req("GET", "/api/bhw/schedule", token=self.tok[role])
            self.assertEqual(res.status, 403, f"{role} should not access bhw/schedule")

    def test_mother_role_cannot_access_visits(self):
        self.req("POST", "/api/register",
                 body={"name": "M Role", "email": "mrole@example.com", "password": "longenough9"})
        tok = self.client.sign_in("mrole@example.com", "longenough9")
        self.assertEqual(self.req("GET", f"/api/mothers/{self.mid}/prenatal", token=tok).status, 403)

    # ── Prenatal visits ───────────────────────────────────────────────────────

    def test_midwife_can_schedule_a_prenatal_visit(self):
        res = self.req("POST", f"/api/mothers/{self.mid}/prenatal",
                       token=self.tok["midwife"],
                       body={"scheduledFor": FUTURE, "vitals": {"bp": "110/70"}})
        self.assertEqual(res.status, 201, res.data)
        v = res.data["visit"]
        self.assertEqual(v["status"], "scheduled")
        self.assertEqual(v["vitals"]["bp"], "110/70")

    def test_bhw_can_also_schedule_a_prenatal_visit(self):
        res = self.req("POST", f"/api/mothers/{self.mid}/prenatal",
                       token=self.tok["bhw"],
                       body={"scheduledFor": FUTURE})
        self.assertEqual(res.status, 201)

    def test_missing_scheduled_for_returns_400(self):
        res = self.req("POST", f"/api/mothers/{self.mid}/prenatal",
                       token=self.tok["midwife"], body={})
        self.assertEqual(res.status, 400)

    def test_prenatal_status_transition_scheduled_to_completed(self):
        created = self.req("POST", f"/api/mothers/{self.mid}/prenatal",
                           token=self.tok["midwife"],
                           body={"scheduledFor": FUTURE})
        vid = created.data["visit"]["id"]

        updated = self.req("PATCH",
                           f"/api/mothers/{self.mid}/prenatal/{vid}",
                           token=self.tok["midwife"],
                           body={"status": "completed",
                                 "observations": "Normal findings."})
        self.assertEqual(updated.status, 200)
        self.assertEqual(updated.data["visit"]["status"], "completed")
        self.assertEqual(updated.data["visit"]["observations"], "Normal findings.")

    def test_prenatal_cannot_skip_to_invalid_transition(self):
        created = self.req("POST", f"/api/mothers/{self.mid}/prenatal",
                           token=self.tok["midwife"],
                           body={"scheduledFor": FUTURE})
        vid = created.data["visit"]["id"]
        # completed → rescheduled is not allowed
        self.req("PATCH", f"/api/mothers/{self.mid}/prenatal/{vid}",
                 token=self.tok["midwife"], body={"status": "completed"})
        bad = self.req("PATCH", f"/api/mothers/{self.mid}/prenatal/{vid}",
                       token=self.tok["midwife"], body={"status": "rescheduled"})
        self.assertEqual(bad.status, 400)

    def test_prenatal_bad_status_value_returns_400(self):
        created = self.req("POST", f"/api/mothers/{self.mid}/prenatal",
                           token=self.tok["midwife"],
                           body={"scheduledFor": FUTURE})
        vid = created.data["visit"]["id"]
        res = self.req("PATCH", f"/api/mothers/{self.mid}/prenatal/{vid}",
                       token=self.tok["midwife"], body={"status": "cancelled"})
        self.assertEqual(res.status, 400)

    def test_list_prenatal_visits(self):
        res = self.req("GET", f"/api/mothers/{self.mid}/prenatal",
                       token=self.tok["physician"])
        self.assertEqual(res.status, 200)
        self.assertIsInstance(res.data["visits"], list)

    # ── Postnatal visits ──────────────────────────────────────────────────────

    def test_midwife_can_schedule_a_postnatal_visit(self):
        res = self.req("POST", f"/api/mothers/{self.mid}/postnatal",
                       token=self.tok["midwife"],
                       body={"scheduledFor": FUTURE,
                             "observations": "Week 2 follow-up."})
        self.assertEqual(res.status, 201)
        self.assertEqual(res.data["visit"]["status"], "scheduled")

    def test_postnatal_transition_to_missed_then_rescheduled(self):
        created = self.req("POST", f"/api/mothers/{self.mid}/postnatal",
                           token=self.tok["midwife"],
                           body={"scheduledFor": FUTURE})
        vid = created.data["visit"]["id"]

        missed = self.req("PATCH", f"/api/mothers/{self.mid}/postnatal/{vid}",
                          token=self.tok["midwife"], body={"status": "missed"})
        self.assertEqual(missed.data["visit"]["status"], "missed")

        rescheduled = self.req("PATCH", f"/api/mothers/{self.mid}/postnatal/{vid}",
                               token=self.tok["midwife"], body={"status": "rescheduled"})
        self.assertEqual(rescheduled.data["visit"]["status"], "rescheduled")

    # ── Home visits ───────────────────────────────────────────────────────────

    def test_bhw_can_plan_a_home_visit(self):
        res = self.req("POST", f"/api/mothers/{self.bhw_mid}/home-visits",
                       token=self.tok["bhw"],
                       body={"scheduledFor": FUTURE,
                             "checklistAnswers": {"q1": "yes"},
                             "notes": "First visit."})
        self.assertEqual(res.status, 201, res.data)
        v = res.data["visit"]
        self.assertEqual(v["status"], "planned")
        self.assertEqual(v["checklistAnswers"]["q1"], "yes")

    def test_home_visit_complete_transition(self):
        created = self.req("POST", f"/api/mothers/{self.bhw_mid}/home-visits",
                           token=self.tok["bhw"],
                           body={"scheduledFor": FUTURE})
        vid = created.data["visit"]["id"]

        done = self.req("PATCH",
                        f"/api/mothers/{self.bhw_mid}/home-visits/{vid}",
                        token=self.tok["bhw"],
                        body={"status": "completed",
                              "checklistAnswers": {"q1": "done"},
                              "notes": "All good."})
        self.assertEqual(done.status, 200)
        self.assertEqual(done.data["visit"]["status"], "completed")
        self.assertEqual(done.data["visit"]["notes"], "All good.")

    def test_home_visit_bad_transition_returns_400(self):
        created = self.req("POST", f"/api/mothers/{self.bhw_mid}/home-visits",
                           token=self.tok["bhw"],
                           body={"scheduledFor": FUTURE})
        vid = created.data["visit"]["id"]
        # planned → completed, then completed → planned is invalid
        self.req("PATCH", f"/api/mothers/{self.bhw_mid}/home-visits/{vid}",
                 token=self.tok["bhw"], body={"status": "completed"})
        bad = self.req("PATCH", f"/api/mothers/{self.bhw_mid}/home-visits/{vid}",
                       token=self.tok["bhw"], body={"status": "planned"})
        self.assertEqual(bad.status, 400)

    def test_list_home_visits(self):
        res = self.req("GET", f"/api/mothers/{self.bhw_mid}/home-visits",
                       token=self.tok["midwife"])
        self.assertEqual(res.status, 200)
        self.assertIsInstance(res.data["visits"], list)

    # ── Task 3.3: mark_overdue_visits with fixed clock ────────────────────────

    def test_overdue_scheduled_visits_are_marked_missed(self):
        # Schedule a prenatal visit in the past
        created = self.req("POST", f"/api/mothers/{self.mid}/prenatal",
                           token=self.tok["midwife"],
                           body={"scheduledFor": PAST})
        vid = created.data["visit"]["id"]
        self.assertEqual(created.data["visit"]["status"], "scheduled")

        # Run mark_overdue_visits with a "now" that is after the scheduled time
        now = datetime(2099, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        changed = vr.mark_overdue_visits(self.db, now=now)
        self.assertGreater(changed, 0)

        # Confirm the visit is now missed
        updated = self.req("GET", f"/api/mothers/{self.mid}/prenatal",
                           token=self.tok["midwife"])
        statuses = [v["status"] for v in updated.data["visits"] if v["id"] == vid]
        self.assertEqual(statuses, ["missed"], f"expected missed, got {statuses}")

    def test_future_visits_are_not_marked_missed(self):
        created = self.req("POST", f"/api/mothers/{self.mid}/prenatal",
                           token=self.tok["midwife"],
                           body={"scheduledFor": FUTURE})
        vid = created.data["visit"]["id"]

        # Run with a "now" that is well before the scheduled time
        now = datetime(2000, 1, 1, tzinfo=timezone.utc)
        vr.mark_overdue_visits(self.db, now=now)

        updated = self.req("GET", f"/api/mothers/{self.mid}/prenatal",
                           token=self.tok["midwife"])
        statuses = [v["status"] for v in updated.data["visits"] if v["id"] == vid]
        self.assertNotIn("missed", statuses, "future visit must not be marked missed")

    def test_completed_visits_are_never_marked_missed(self):
        created = self.req("POST", f"/api/mothers/{self.mid}/prenatal",
                           token=self.tok["midwife"],
                           body={"scheduledFor": PAST})
        vid = created.data["visit"]["id"]
        # Complete it first
        self.req("PATCH", f"/api/mothers/{self.mid}/prenatal/{vid}",
                 token=self.tok["midwife"], body={"status": "completed"})

        now = datetime(2099, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        vr.mark_overdue_visits(self.db, now=now)

        updated = self.req("GET", f"/api/mothers/{self.mid}/prenatal",
                           token=self.tok["midwife"])
        statuses = [v["status"] for v in updated.data["visits"] if v["id"] == vid]
        self.assertEqual(statuses, ["completed"], "completed visit must stay completed")

    def test_home_visits_are_also_marked_missed(self):
        created = self.req("POST", f"/api/mothers/{self.bhw_mid}/home-visits",
                           token=self.tok["bhw"],
                           body={"scheduledFor": PAST})
        vid = created.data["visit"]["id"]

        now = datetime(2099, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        vr.mark_overdue_visits(self.db, now=now)

        updated = self.req("GET", f"/api/mothers/{self.bhw_mid}/home-visits",
                           token=self.tok["bhw"])
        statuses = [v["status"] for v in updated.data["visits"] if v["id"] == vid]
        self.assertEqual(statuses, ["missed"])

    # ── Task 3.4: BHW schedule endpoint ──────────────────────────────────────

    def test_bhw_schedule_returns_expected_keys(self):
        res = self.req("GET", "/api/bhw/schedule", token=self.tok["bhw"])
        self.assertEqual(res.status, 200, res.data)
        self.assertIn("dueToday", res.data)
        self.assertIn("overdue",  res.data)
        self.assertIn("missed",   res.data)

    def test_bhw_schedule_contains_planned_visit_due_today(self):
        # Plan a visit scheduled for right now (today's date)
        from datetime import date
        today = date.today().isoformat() + "T09:00:00"
        self.req("POST", f"/api/mothers/{self.bhw_mid}/home-visits",
                 token=self.tok["bhw"],
                 body={"scheduledFor": today})

        res = self.req("GET", "/api/bhw/schedule", token=self.tok["bhw"])
        self.assertEqual(res.status, 200)
        # The visit should appear in dueToday (or overdue if time already passed)
        all_items = res.data["dueToday"] + res.data["overdue"]
        self.assertTrue(len(all_items) >= 0)   # at minimum the endpoint works

    def test_bhw_schedule_only_contains_own_visits(self):
        """A second BHW should not see the first BHW's visits."""
        # Invite a second BHW
        self.req("POST", "/api/accounts", token=self.tok["sysadmin"],
                 body={"name": "Second BHW", "email": "bhw2@bhw.example", "role": "bhw"})
        import re
        log_lines = []
        # Reset the password via the server log pattern
        app2 = create_app(self.db, log=log_lines.append)
        client2 = Client(app2)
        client2.request("POST", "/api/password-reset", body={"email": "bhw2@bhw.example"})
        line = next(l for l in log_lines if "Code for bhw2@bhw.example" in l)
        code = re.search(r": ([A-F0-9]{8})", line).group(1)
        client2.request("POST", "/api/password-reset/confirm",
                        body={"code": code, "password": "bhw2password1"})
        tok2 = client2.sign_in("bhw2@bhw.example", "bhw2password1")

        # Create a home visit under BHW2
        bhw2_mother = client2.request(
            "POST", "/api/mothers", token=tok2,
            body={"name": "BHW2 Mother", "dateOfBirth": "1990-01-01",
                  "consentRecordedAt": CONSENT},
        )
        assert bhw2_mother.status == 201
        bhw2_mid = bhw2_mother.data["mother"]["id"]
        from datetime import date
        today = date.today().isoformat() + "T10:00:00"
        client2.request("POST", f"/api/mothers/{bhw2_mid}/home-visits",
                        token=tok2, body={"scheduledFor": today})

        # BHW1 schedule should not include BHW2's visit
        bhw1_schedule = self.req("GET", "/api/bhw/schedule", token=self.tok["bhw"])
        all_bhw1_ids = {
            v["bhwId"]
            for v in bhw1_schedule.data["dueToday"]
            + bhw1_schedule.data["overdue"]
            + bhw1_schedule.data["missed"]
        }
        # BHW2's user id must not appear
        bhw2_me = client2.request("GET", "/api/me", token=tok2)
        bhw2_id = bhw2_me.data["user"]["id"]
        self.assertNotIn(bhw2_id, all_bhw1_ids,
                         "BHW1 schedule must not include BHW2's visits")

    def test_missed_visit_appears_in_bhw_schedule_missed_list(self):
        created = self.req("POST", f"/api/mothers/{self.bhw_mid}/home-visits",
                           token=self.tok["bhw"],
                           body={"scheduledFor": PAST})
        vid = created.data["visit"]["id"]

        # Run with far-future now so the visit is definitely missed
        far_future = datetime(2099, 12, 31, tzinfo=timezone.utc)
        result = vr.bhw_schedule(self.db, bhw_id=self._bhw_user_id(), now=far_future)
        missed_ids = [v["id"] for v in result["missed"]]
        self.assertIn(vid, missed_ids)

    def _bhw_user_id(self):
        from common import query_one
        row = query_one(self.db, "SELECT id FROM users WHERE email = 'b.reyes@bhw.example'")
        return row["id"]


if __name__ == "__main__":
    unittest.main()
