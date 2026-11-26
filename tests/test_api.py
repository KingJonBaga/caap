"""Automated tests for the NURTURA server. Run with: py -m unittest discover -s tests

The tests call the app directly with an in-memory database, so they need no running server."""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAFF_PASSWORD = "test-only-staff-pass"  # used for the test database only
sys.path.insert(0, str(ROOT / "server"))

from app import create_app  # noqa: E402
from db import open_database  # noqa: E402


class Result:
    def __init__(self, status, content_type, body, headers):
        self.status = status
        self.content_type = content_type
        self.body = body
        self.headers = headers
        self.data = json.loads(body) if content_type and content_type.startswith("application/json") else body


class Client:
    def __init__(self, app, log_lines):
        self.app = app
        self.log_lines = log_lines

    def request(self, method, path, body=None, token=None, origin=None):
        headers = {}
        raw = b""
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            headers["content-type"] = "application/json"
        if token:
            headers["authorization"] = "Bearer " + token
        if origin:
            headers["origin"] = origin
        return Result(*self.app.handle(method, path, headers, raw))

    def sign_in(self, email, password):
        res = self.request("POST", "/api/login", body={"email": email, "password": password})
        assert res.status == 200, f"login failed for {email}"
        return res.data["token"]


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.log_lines = []
        cls.db = open_database(":memory:", shared_password=STAFF_PASSWORD)
        cls.app = create_app(cls.db, log=cls.log_lines.append)
        cls.client = Client(cls.app, cls.log_lines)
        cls.tokens = {
            # BHW-RHU roles (new seed)
            "bhw":       cls.client.sign_in("b.reyes@bhw.example",   STAFF_PASSWORD),
            "midwife":   cls.client.sign_in("c.lim@rhu.example",     STAFF_PASSWORD),
            "physician": cls.client.sign_in("a.okafor@rhu.example",  STAFF_PASSWORD),
            "rhu_admin": cls.client.sign_in("rhu.admin@rhu.example", STAFF_PASSWORD),
            "sysadmin":  cls.client.sign_in("admin@example.com",     STAFF_PASSWORD),
            # Convenience aliases so tests can still use "nurse", "provider", "admin"
            "nurse":    cls.client.sign_in("c.lim@rhu.example",     STAFF_PASSWORD),
            "provider": cls.client.sign_in("a.okafor@rhu.example",  STAFF_PASSWORD),
            "admin":    cls.client.sign_in("admin@example.com",     STAFF_PASSWORD),
        }

    def req(self, method, path, **kwargs):
        return self.client.request(method, path, **kwargs)

    def reset_code_for(self, email, before):
        line = next(text for text in self.log_lines[before:] if "Code for " + email in text)
        return re.search(r": ([A-F0-9]{8})", line).group(1)

    # ── Sign-in and sessions ────────────────────────────────

    def test_seeded_accounts_sign_in_with_their_roles(self):
        res = self.req("POST", "/api/login", body={"email": "b.reyes@bhw.example", "password": STAFF_PASSWORD})
        self.assertEqual(res.data["user"]["role"], "bhw")
        self.assertGreaterEqual(len(res.data["token"]), 32)
        self.assertNotIn("password_hash", res.data["user"], "password hash must never be sent")

    def test_wrong_password_and_unknown_email_are_rejected_the_same_way(self):
        wrong = self.req("POST", "/api/login", body={"email": "c.lim@rhu.example", "password": "nope-nope"})
        unknown = self.req("POST", "/api/login", body={"email": "nobody@example.com", "password": STAFF_PASSWORD})
        self.assertEqual(wrong.status, 401)
        self.assertEqual(unknown.status, 401)
        self.assertEqual(wrong.data["error"], unknown.data["error"])

    def test_requests_without_a_valid_token_get_401(self):
        self.assertEqual(self.req("GET", "/api/caretaker").status, 401)
        self.assertEqual(self.req("GET", "/api/caretaker", token="not-a-real-token").status, 401)

    def test_logout_ends_the_session(self):
        token = self.client.sign_in("admin@example.com", STAFF_PASSWORD)
        self.assertEqual(self.req("GET", "/api/me", token=token).data["user"]["role"], "sysadmin")
        self.req("DELETE", "/api/session", token=token)
        self.assertEqual(self.req("GET", "/api/caretaker", token=token).status, 401)

    # ── Role rules ──────────────────────────────────────────

    def test_bhw_cannot_read_patient_records(self):
        self.assertEqual(self.req("GET", "/api/patients", token=self.tokens["bhw"]).status, 200,
                         "bhw is in CARE_TEAM and may read patients")

    def test_midwife_and_physician_can_read_patient_records(self):
        self.assertEqual(self.req("GET", "/api/patients", token=self.tokens["midwife"]).status, 200)
        self.assertEqual(self.req("GET", "/api/patients/MH-2025-0148", token=self.tokens["physician"]).status, 200)

    def test_only_admins_can_read_the_audit_log(self):
        self.assertEqual(self.req("GET", "/api/audit", token=self.tokens["midwife"]).status, 403)
        self.assertEqual(self.req("GET", "/api/audit", token=self.tokens["bhw"]).status, 403)
        # sysadmin and rhu_admin can both read the audit log
        res = self.req("GET", "/api/audit", token=self.tokens["sysadmin"])
        self.assertEqual(res.status, 200)
        self.assertTrue(any(event["action"] == "login" for event in res.data["events"]))
        self.assertEqual(self.req("GET", "/api/audit", token=self.tokens["rhu_admin"]).status, 200)

    def test_caretaker_endpoint_requires_sign_in(self):
        self.assertEqual(self.req("GET", "/api/caretaker").status, 401)

    # ── Messages and visits ─────────────────────────────────

    def test_a_midwife_can_message_via_the_api(self):
        sent = self.req("POST", "/api/messages", token=self.tokens["midwife"],
                        body={"patientId": "MH-2025-0148", "body": "Reminder: bring your records."})
        self.assertEqual(sent.status, 201)

    def test_empty_messages_are_refused(self):
        empty = self.req("POST", "/api/messages", token=self.tokens["midwife"],
                         body={"patientId": "MH-2025-0148", "body": "   "})
        self.assertEqual(empty.status, 400)

    def test_a_midwife_can_add_an_appointment(self):
        created = self.req("POST", "/api/visits", token=self.tokens["midwife"], body={
            "patientId": "MH-2025-0148", "patientName": "Maria Santos", "startsOn": "2026-12-17",
            "startsAt": "11:00", "title": "Blood pressure check", "room": "Room 2",
        })
        self.assertEqual(created.status, 201)

    def test_appointments_need_a_date_time_and_visit_type(self):
        res = self.req("POST", "/api/visits", token=self.tokens["midwife"],
                       body={"patientName": "Test Patient", "startsOn": "", "startsAt": "09:00", "title": "Check"})
        self.assertEqual(res.status, 400)

    def test_moving_a_visit_to_another_day_in_the_week_saves_the_change(self):
        visits = self.req("GET", "/api/visits?from=2026-11-30&to=2026-12-04", token=self.tokens["midwife"])
        first = visits.data["visits"][0]
        moved = self.req("PATCH", f"/api/visits/{first['id']}", token=self.tokens["midwife"], body={"startsOn": "2026-12-01"})
        self.assertEqual(moved.status, 200)
        self.assertEqual(moved.data["visit"]["starts_on"], "2026-12-01")

    def test_caseload_notes_are_saved_and_listed(self):
        added = self.req("POST", "/api/notes", token=self.tokens["physician"],
                         body={"body": "Call patient about her week-4 screening."})
        self.assertEqual(added.status, 201)
        listed = self.req("GET", "/api/notes", token=self.tokens["midwife"])
        self.assertTrue(any("week-4 screening" in n["body"] for n in listed.data["notes"]))

    # ── Register and password reset ─────────────────────────

    def test_a_new_patient_can_register_and_then_sign_in(self):
        email = "new.patient@example.com"
        created = self.req("POST", "/api/register", body={"name": "New Patient", "email": email, "password": "longenough1"})
        self.assertEqual(created.status, 201)
        self.assertIn(created.data["user"]["role"], ("patient", "mother"),
                      "self-registration should produce a patient or mother role")
        self.assertEqual(self.req("POST", "/api/login", body={"email": email, "password": "longenough1"}).status, 200)

    def test_duplicate_emails_and_weak_passwords_are_refused(self):
        dup = self.req("POST", "/api/register", body={"name": "Again", "email": "new.patient@example.com", "password": "longenough1"})
        self.assertEqual(dup.status, 409)
        weak = self.req("POST", "/api/register", body={"name": "Weak", "email": "weak@example.com", "password": "short"})
        self.assertEqual(weak.status, 400)

    def test_password_reset_code_goes_to_the_server_log_and_changes_the_password(self):
        email = "new.patient@example.com"
        before = len(self.log_lines)
        requested = self.req("POST", "/api/password-reset", body={"email": email})
        self.assertEqual(requested.status, 200)
        code = self.reset_code_for(email, before)
        confirmed = self.req("POST", "/api/password-reset/confirm", body={"code": code, "password": "brandnewpass1"})
        self.assertEqual(confirmed.status, 200)
        self.assertEqual(self.req("POST", "/api/login", body={"email": email, "password": "longenough1"}).status, 401)
        self.assertEqual(self.req("POST", "/api/login", body={"email": email, "password": "brandnewpass1"}).status, 200)

    def test_a_reset_code_can_only_be_used_once_and_unknown_emails_get_the_same_reply(self):
        email = "new.patient@example.com"
        before = len(self.log_lines)
        self.req("POST", "/api/password-reset", body={"email": email})
        code = self.reset_code_for(email, before)
        self.assertEqual(self.req("POST", "/api/password-reset/confirm", body={"code": code, "password": "anotherpass1"}).status, 200)
        self.assertEqual(self.req("POST", "/api/password-reset/confirm", body={"code": code, "password": "anotherpass2"}).status, 400)
        self.assertEqual(self.req("POST", "/api/password-reset", body={"email": "nobody@example.com"}).status, 200)

    # ── Static file safety ──────────────────────────────────

    def test_server_code_tests_and_the_database_folder_are_not_served(self):
        self.assertEqual(self.req("GET", "/server/db.py").status, 404)
        self.assertEqual(self.req("GET", "/tests/test_api.py").status, 404)
        self.assertEqual(self.req("GET", "/data/caap.db").status, 404)
        self.assertEqual(self.req("GET", "/%2e%2e/%2e%2e/secret.txt").status, 404)

    def test_pages_opened_from_a_file_can_call_the_api_and_other_websites_cannot(self):
        allowed = self.req("OPTIONS", "/api/login", origin="null")
        self.assertEqual(allowed.status, 204)
        self.assertEqual(allowed.headers.get("Access-Control-Allow-Origin"), "null")
        blocked = self.req("OPTIONS", "/api/login", origin="https://evil.example")
        self.assertNotIn("Access-Control-Allow-Origin", blocked.headers)

    # ── Admin: accounts ─────────────────────────────────────

    def test_admins_see_every_account_with_its_status_and_other_roles_cannot(self):
        res = self.req("GET", "/api/accounts", token=self.tokens["sysadmin"])
        self.assertEqual(res.status, 200)
        self.assertTrue(any(a["email"] == "b.reyes@bhw.example" and a["status"] == "active" for a in res.data["accounts"]))
        self.assertTrue(all(a["accountId"].startswith("ACC-") for a in res.data["accounts"]))
        # midwife cannot list accounts
        self.assertEqual(self.req("GET", "/api/accounts", token=self.tokens["midwife"]).status, 403)
        # rhu_admin can also list accounts
        self.assertEqual(self.req("GET", "/api/accounts", token=self.tokens["rhu_admin"]).status, 200)

    def test_an_invited_account_has_no_known_password_until_the_person_sets_one(self):
        invited = self.req("POST", "/api/accounts", token=self.tokens["sysadmin"],
                           body={"name": "Invited Midwife", "email": "invited.midwife@example.com", "role": "midwife"})
        self.assertEqual(invited.status, 201)
        self.assertEqual(invited.data["account"]["role"], "midwife")
        self.assertNotIn("password_hash", invited.data["account"])
        self.assertEqual(self.req("POST", "/api/login", body={"email": "invited.midwife@example.com", "password": "guess-guess"}).status, 401)

        before = len(self.log_lines)
        self.req("POST", "/api/password-reset", body={"email": "invited.midwife@example.com"})
        code = self.reset_code_for("invited.midwife@example.com", before)
        self.req("POST", "/api/password-reset/confirm", body={"code": code, "password": "invitedmidwife1"})
        self.assertEqual(self.req("POST", "/api/login", body={"email": "invited.midwife@example.com", "password": "invitedmidwife1"}).status, 200)

    def test_suspending_an_account_blocks_sign_in_and_ends_its_sessions_and_activating_restores_it(self):
        accounts = self.req("GET", "/api/accounts", token=self.tokens["sysadmin"])
        target = next(a for a in accounts.data["accounts"] if a["email"] == "invited.midwife@example.com")
        midwife_token = self.client.sign_in("invited.midwife@example.com", "invitedmidwife1")

        suspended = self.req("PATCH", f"/api/accounts/{target['id']}", token=self.tokens["sysadmin"], body={"status": "suspended"})
        self.assertEqual(suspended.status, 200)
        self.assertEqual(suspended.data["account"]["status"], "suspended")
        self.assertEqual(self.req("GET", "/api/visits", token=midwife_token).status, 401, "open session is ended")

        blocked = self.req("POST", "/api/login", body={"email": "invited.midwife@example.com", "password": "invitedmidwife1"})
        self.assertEqual(blocked.status, 403)
        self.assertIn("suspended", blocked.data["error"])

        self.req("PATCH", f"/api/accounts/{target['id']}", token=self.tokens["sysadmin"], body={"status": "active"})
        self.assertEqual(self.req("POST", "/api/login", body={"email": "invited.midwife@example.com", "password": "invitedmidwife1"}).status, 200)

    def test_admins_cannot_suspend_themselves_and_bad_statuses_or_roles_are_refused(self):
        me = self.req("GET", "/api/me", token=self.tokens["sysadmin"])
        accounts = self.req("GET", "/api/accounts", token=self.tokens["sysadmin"])
        admin_row = next(a for a in accounts.data["accounts"] if a["email"] == "admin@example.com")
        self.assertEqual(me.data["user"]["id"], admin_row["id"])
        self.assertEqual(self.req("PATCH", f"/api/accounts/{admin_row['id']}", token=self.tokens["sysadmin"], body={"status": "suspended"}).status, 400)
        self.assertEqual(self.req("PATCH", f"/api/accounts/{admin_row['id']}", token=self.tokens["sysadmin"], body={"status": "deleted"}).status, 400)
        self.assertEqual(self.req("POST", "/api/accounts", token=self.tokens["sysadmin"],
                                  body={"name": "X Y", "email": "x.y@example.com", "role": "superuser"}).status, 400)
        # Duplicate email check — use an email that exists in the new seed
        self.assertEqual(self.req("POST", "/api/accounts", token=self.tokens["sysadmin"],
                                  body={"name": "Dup", "email": "b.reyes@bhw.example", "role": "midwife"}).status, 409)

    def test_the_site_itself_is_served(self):
        res = self.req("GET", "/index.html")
        self.assertEqual(res.status, 200)
        self.assertIn("text/html", res.content_type)

    # ── BHW-RHU role validation ─────────────────────────────

    def test_new_bhw_rhu_roles_are_accepted_on_invite(self):
        for role in ("bhw", "midwife", "physician", "rhu_admin", "sysadmin", "mother"):
            res = self.req("POST", "/api/accounts", token=self.tokens["sysadmin"],
                           body={"name": f"Test {role}", "email": f"test.{role}@example.com", "role": role})
            self.assertEqual(res.status, 201, f"role '{role}' should be accepted, got {res.status}: {res.data}")

    def test_invalid_role_on_invite_returns_400(self):
        res = self.req("POST", "/api/accounts", token=self.tokens["sysadmin"],
                       body={"name": "Bad", "email": "bad.role@example.com", "role": "superuser"})
        self.assertEqual(res.status, 400)

    def test_rhu_admin_can_invite_accounts(self):
        res = self.req("POST", "/api/accounts", token=self.tokens["rhu_admin"],
                       body={"name": "RHU BHW", "email": "rhu.bhw@example.com", "role": "bhw"})
        self.assertEqual(res.status, 201)

    def test_bhw_cannot_invite_accounts(self):
        res = self.req("POST", "/api/accounts", token=self.tokens["bhw"],
                       body={"name": "X", "email": "x@example.com", "role": "bhw"})
        self.assertEqual(res.status, 403)

    def test_all_new_seed_roles_sign_in_correctly(self):
        expected = {
            "b.reyes@bhw.example":   "bhw",
            "c.lim@rhu.example":     "midwife",
            "a.okafor@rhu.example":  "physician",
            "rhu.admin@rhu.example": "rhu_admin",
            "admin@example.com":     "sysadmin",
        }
        for email, role in expected.items():
            res = self.req("POST", "/api/login", body={"email": email, "password": STAFF_PASSWORD})
            self.assertEqual(res.status, 200, f"login failed for {email}")
            self.assertEqual(res.data["user"]["role"], role, f"wrong role for {email}")

    # ── Mothers: duplicate registration (task 2.3) ──────────

    def test_a_new_mother_can_be_registered_by_a_bhw(self):
        res = self.req("POST", "/api/mothers", token=self.tokens["bhw"], body={
            "name": "Ana Reyes",
            "dateOfBirth": "1995-04-10",
            "consentRecordedAt": "2025-01-15T08:00:00Z",
        })
        self.assertEqual(res.status, 201)
        self.assertEqual(res.data["mother"]["name"], "Ana Reyes")

    def test_registering_the_same_name_and_dob_twice_returns_409(self):
        payload = {
            "name": "Luisa Cruz",
            "dateOfBirth": "1990-07-22",
            "consentRecordedAt": "2025-01-15T08:00:00Z",
        }
        first = self.req("POST", "/api/mothers", token=self.tokens["bhw"], body=payload)
        self.assertEqual(first.status, 201)
        second = self.req("POST", "/api/mothers", token=self.tokens["bhw"], body=payload)
        self.assertEqual(second.status, 409)
        self.assertIn("already registered", second.data["error"])

    def test_duplicate_key_normalises_name_casing_and_whitespace(self):
        # First registration uses mixed case and normal spacing.
        first = self.req("POST", "/api/mothers", token=self.tokens["midwife"], body={
            "name": "Maria Santos",
            "dateOfBirth": "1988-03-05",
            "consentRecordedAt": "2025-01-15T08:00:00Z",
        })
        self.assertEqual(first.status, 201)
        # Second uses all-lowercase with an extra internal space — same duplicate_key.
        second = self.req("POST", "/api/mothers", token=self.tokens["midwife"], body={
            "name": "maria  santos",
            "dateOfBirth": "1988-03-05",
            "consentRecordedAt": "2025-01-15T08:00:00Z",
        })
        self.assertEqual(second.status, 409)
        self.assertIn("already registered", second.data["error"])

    def test_saving_a_mother_without_consent_returns_400(self):
        res = self.req("POST", "/api/mothers", token=self.tokens["bhw"], body={
            "name": "No Consent Mother",
            "dateOfBirth": "2000-01-01",
            # consentRecordedAt intentionally omitted
        })
        self.assertEqual(res.status, 400)
        self.assertIn("Consent", res.data["error"])

    def test_saving_a_mother_with_empty_consent_returns_400(self):
        res = self.req("POST", "/api/mothers", token=self.tokens["bhw"], body={
            "name": "Empty Consent Mother",
            "dateOfBirth": "2000-02-02",
            "consentRecordedAt": "",
        })
        self.assertEqual(res.status, 400)
        self.assertIn("Consent", res.data["error"])

    # ── Mothers: audit log (task 2.5) ───────────────────────

    def test_reading_a_mother_record_writes_a_mother_view_audit_row(self):
        # Register a fresh mother so we have a known id.
        created = self.req("POST", "/api/mothers", token=self.tokens["bhw"], body={
            "name": "Audit Test Mother",
            "dateOfBirth": "1992-06-15",
            "consentRecordedAt": "2025-01-15T08:00:00Z",
        })
        self.assertEqual(created.status, 201)
        mother_id = created.data["mother"]["id"]

        # BHW reads the individual record.
        get_res = self.req("GET", f"/api/mothers/{mother_id}", token=self.tokens["bhw"])
        self.assertEqual(get_res.status, 200)

        # Admin checks the audit log for the expected row.
        audit_res = self.req("GET", "/api/audit", token=self.tokens["sysadmin"])
        self.assertEqual(audit_res.status, 200)
        self.assertTrue(
            any(
                e["action"] == "mother_view" and e["detail"] == str(mother_id)
                for e in audit_res.data["events"]
            ),
            f"Expected a mother_view audit row with detail='{mother_id}' but none found",
        )

    def test_reading_a_mothers_history_writes_a_mother_view_audit_row(self):
        # Register a fresh mother so we have a known id.
        created = self.req("POST", "/api/mothers", token=self.tokens["bhw"], body={
            "name": "Audit History Mother",
            "dateOfBirth": "1994-09-20",
            "consentRecordedAt": "2025-01-15T08:00:00Z",
        })
        self.assertEqual(created.status, 201)
        mother_id = created.data["mother"]["id"]

        # BHW reads the history endpoint.
        hist_res = self.req("GET", f"/api/mothers/{mother_id}/history", token=self.tokens["bhw"])
        self.assertEqual(hist_res.status, 200)

        # Admin checks the audit log for the expected row.
        audit_res = self.req("GET", "/api/audit", token=self.tokens["sysadmin"])
        self.assertEqual(audit_res.status, 200)
        self.assertTrue(
            any(
                e["action"] == "mother_view" and e["detail"] == str(mother_id)
                for e in audit_res.data["events"]
            ),
            f"Expected a mother_view audit row with detail='{mother_id}' but none found",
        )


    # ── BHW schedule: due-today / overdue / missed (task 3.4) ───────────────

    def test_bhw_schedule_requires_authentication(self):
        """Unauthenticated request must return 401."""
        res = self.req("GET", "/api/bhw/schedule")
        self.assertEqual(res.status, 401)

    def test_bhw_schedule_refuses_non_bhw_roles(self):
        """Midwife, physician and rhu_admin must each receive 403."""
        for role in ("midwife", "physician", "rhu_admin"):
            res = self.req("GET", "/api/bhw/schedule", token=self.tokens[role])
            self.assertEqual(res.status, 403, f"expected 403 for role '{role}'")

    def test_bhw_schedule_returns_correct_categories(self):
        """Response must contain the three category keys."""
        res = self.req("GET", "/api/bhw/schedule", token=self.tokens["bhw"])
        self.assertEqual(res.status, 200)
        self.assertIn("dueToday", res.data)
        self.assertIn("overdue",  res.data)
        self.assertIn("missed",   res.data)

    def _register_mother_for_bhw(self, bhw_token, name, suffix=""):
        """Helper: register a mother and return her id."""
        res = self.req("POST", "/api/mothers", token=bhw_token, body={
            "name": name + suffix,
            "dateOfBirth": "1990-01-01",
            "consentRecordedAt": "2025-01-01T00:00:00Z",
        })
        self.assertEqual(res.status, 201, f"failed to create mother '{name + suffix}': {res.data}")
        return res.data["mother"]["id"]

    def test_bhw_schedule_categorises_visits_correctly(self):
        """A home visit scheduled yesterday → overdue or missed (after mark runs),
        one due today → dueToday, one explicitly marked missed → missed."""
        import datetime

        bhw_token = self.tokens["bhw"]
        mid = self._register_mother_for_bhw(bhw_token, "Schedule Category Mother", " 3.4A")

        # Yesterday (overdue – not yet auto-marked missed because mark_overdue uses full datetime)
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        # Today
        today = datetime.date.today().isoformat()
        # Far future (not in any list)
        future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()

        # Create a home visit for yesterday → will appear as overdue or missed after the mark pass
        hv_res = self.req("POST", "/api/home-visits", token=bhw_token, body={
            "motherId": mid,
            "scheduledFor": yesterday + "T09:00",
        })
        self.assertEqual(hv_res.status, 201)

        # Create a home visit for today → dueToday
        hv_today = self.req("POST", "/api/home-visits", token=bhw_token, body={
            "motherId": mid,
            "scheduledFor": today + "T10:00",
        })
        self.assertEqual(hv_today.status, 201)

        # Create a prenatal visit for yesterday → will be overdue or missed
        pv_res = self.req("POST", "/api/prenatal-visits", token=bhw_token, body={
            "motherId": mid,
            "scheduledFor": yesterday + "T08:00",
        })
        self.assertEqual(pv_res.status, 201)

        # Fetch the schedule – mark_overdue_visits runs inside bhw_schedule
        schedule = self.req("GET", "/api/bhw/schedule", token=bhw_token)
        self.assertEqual(schedule.status, 200)

        all_ids = (
            [v["id"] for v in schedule.data["dueToday"]]
            + [v["id"] for v in schedule.data["overdue"]]
            + [v["id"] for v in schedule.data["missed"]]
        )

        # The today home visit must be in dueToday
        due_ids = [v["id"] for v in schedule.data["dueToday"]]
        self.assertIn(hv_today.data["visit"]["id"], due_ids,
                      "home visit due today should be in dueToday")

        # The yesterday home visit must appear somewhere (overdue or missed)
        self.assertIn(hv_res.data["visit"]["id"], all_ids,
                      "yesterday home visit should appear in overdue or missed")

        # The yesterday prenatal visit must appear somewhere
        self.assertIn(pv_res.data["visit"]["id"], all_ids,
                      "yesterday prenatal visit should appear in overdue or missed")

    def test_bhw_schedule_returns_only_own_visits(self):
        """A second BHW must not see the first BHW's visits, and vice-versa."""
        # Invite a second BHW account via the admin
        invite_res = self.req("POST", "/api/accounts", token=self.tokens["sysadmin"], body={
            "name": "Second BHW",
            "email": "bhw2.test@bhw.example",
            "role": "bhw",
        })
        self.assertEqual(invite_res.status, 201)

        # Give the new BHW a known password by using the password-reset flow
        import re as _re
        log_before = len(self.log_lines)
        self.req("POST", "/api/password-reset", body={"email": "bhw2.test@bhw.example"})
        code = self.reset_code_for("bhw2.test@bhw.example", log_before)
        set_res = self.req("POST", "/api/password-reset/confirm", body={
            "email": "bhw2.test@bhw.example",
            "code": code,
            "password": "BHW2pass!99",
        })
        self.assertEqual(set_res.status, 200)

        bhw2_token = self.client.sign_in("bhw2.test@bhw.example", "BHW2pass!99")
        self.assertIsNotNone(bhw2_token)

        # BHW1 registers a mother and creates a home visit
        import datetime
        today = datetime.date.today().isoformat()
        bhw1_token = self.tokens["bhw"]
        bhw1_mid = self._register_mother_for_bhw(bhw1_token, "BHW1 Isolation Mother", " 3.4B")
        hv1 = self.req("POST", "/api/home-visits", token=bhw1_token, body={
            "motherId": bhw1_mid,
            "scheduledFor": today + "T09:00",
        })
        self.assertEqual(hv1.status, 201)

        # BHW2 registers a different mother and creates a home visit
        bhw2_mid = self._register_mother_for_bhw(bhw2_token, "BHW2 Isolation Mother", " 3.4B")
        hv2 = self.req("POST", "/api/home-visits", token=bhw2_token, body={
            "motherId": bhw2_mid,
            "scheduledFor": today + "T09:00",
        })
        self.assertEqual(hv2.status, 201)

        # BHW1's schedule should contain BHW1's visit but not BHW2's
        s1 = self.req("GET", "/api/bhw/schedule", token=bhw1_token)
        s1_all_ids = (
            [v["id"] for v in s1.data["dueToday"]]
            + [v["id"] for v in s1.data["overdue"]]
            + [v["id"] for v in s1.data["missed"]]
        )
        self.assertIn(hv1.data["visit"]["id"],    s1_all_ids,
                      "BHW1 visit should appear in BHW1's schedule")
        self.assertNotIn(hv2.data["visit"]["id"], s1_all_ids,
                         "BHW2 visit must not appear in BHW1's schedule")

        # BHW2's schedule should contain BHW2's visit but not BHW1's
        s2 = self.req("GET", "/api/bhw/schedule", token=bhw2_token)
        s2_all_ids = (
            [v["id"] for v in s2.data["dueToday"]]
            + [v["id"] for v in s2.data["overdue"]]
            + [v["id"] for v in s2.data["missed"]]
        )
        self.assertIn(hv2.data["visit"]["id"],    s2_all_ids,
                      "BHW2 visit should appear in BHW2's schedule")
        self.assertNotIn(hv1.data["visit"]["id"], s2_all_ids,
                         "BHW1 visit must not appear in BHW2's schedule")


    # ── Risk indicators (task 5.2) ──────────────────────────────────────────────

    def test_admin_can_create_a_risk_indicator(self):
        res = self.req("POST", "/api/indicators", token=self.tokens["rhu_admin"], body={
            "code": "RI-001",
            "description": "High blood pressure during pregnancy",
            "source_reference": "DOH Maternal Care Guidelines 2023, Section 4.2",
            "approved_by": "Dr. A. Okafor",
            "approved_at": "2025-01-10T00:00:00Z",
        })
        self.assertEqual(res.status, 201)
        self.assertEqual(res.data["indicator"]["code"], "RI-001")
        self.assertIn("source_reference", res.data["indicator"])
        self.assertIn("approved_by", res.data["indicator"])

    def test_creating_indicator_without_source_reference_returns_400(self):
        res = self.req("POST", "/api/indicators", token=self.tokens["rhu_admin"], body={
            "code": "RI-NOSRC",
            "description": "Missing source",
            "approved_by": "Dr. A. Okafor",
            # source_reference intentionally omitted
        })
        self.assertEqual(res.status, 400)

    def test_creating_indicator_without_approved_by_returns_400(self):
        res = self.req("POST", "/api/indicators", token=self.tokens["rhu_admin"], body={
            "code": "RI-NOAPPR",
            "description": "Missing approver",
            "source_reference": "Some guideline reference",
            # approved_by intentionally omitted
        })
        self.assertEqual(res.status, 400)

    def test_non_admin_cannot_create_indicator(self):
        for role in ("bhw", "midwife"):
            res = self.req("POST", "/api/indicators", token=self.tokens[role], body={
                "code": "RI-FORBIDDEN",
                "source_reference": "Ref",
                "approved_by": "Dr. X",
            })
            self.assertEqual(res.status, 403, f"expected 403 for role '{role}'")

    def test_unauthenticated_request_to_indicators_returns_401(self):
        res = self.req("POST", "/api/indicators", body={
            "code": "RI-NOAUTH",
            "source_reference": "Ref",
            "approved_by": "Dr. X",
        })
        self.assertEqual(res.status, 401)

    def test_admin_can_list_indicators(self):
        # Ensure at least one indicator exists (may have been created by earlier test)
        self.req("POST", "/api/indicators", token=self.tokens["sysadmin"], body={
            "code": "RI-LIST-TEST",
            "source_reference": "DOH Guidelines",
            "approved_by": "Dr. A. Okafor",
        })
        res = self.req("GET", "/api/indicators", token=self.tokens["rhu_admin"])
        self.assertEqual(res.status, 200)
        self.assertIn("indicators", res.data)
        self.assertTrue(any(i["code"] == "RI-LIST-TEST" for i in res.data["indicators"]))

    # ── RHU open-flags queue (task 5.4) ────────────────────────────────────────

    def _seed_open_flag(self, token):
        """Create a mother, then insert an open risk flag directly and return its mother_id."""
        mother_res = self.req("POST", "/api/mothers", token=token, body={
            "name": "Flag Queue Test Mother",
            "dateOfBirth": "1993-11-01",
            "consentRecordedAt": "2025-01-15T08:00:00Z",
        })
        # Accept 201 (new) or 409 (already exists from a previous run) and retrieve the id.
        if mother_res.status == 201:
            mother_id = mother_res.data["mother"]["id"]
        else:
            # Fetch existing record by listing – use a unique enough name so we can find it.
            list_res = self.req("GET", "/api/mothers", token=token)
            self.assertEqual(list_res.status, 200)
            match = next(
                (m for m in list_res.data["mothers"] if "Flag Queue Test Mother" in m["name"]),
                None,
            )
            self.assertIsNotNone(match, "Could not find Flag Queue Test Mother in mothers list")
            mother_id = match["id"]

        # Insert an open flag directly via SQL so we don't need a flag-creation endpoint yet.
        user_id = self.req("GET", "/api/me", token=token).data["user"]["id"]
        self.db.execute(
            """INSERT INTO risk_flags (mother_id, observation, raised_by, status, created_by)
               VALUES (?, 'Test observation for queue', ?, 'open', ?)""",
            (mother_id, user_id, user_id),
        )
        self.db.execute("COMMIT") if False else None   # isolation_level=None → auto-commit
        return mother_id

    def test_flags_queue_requires_authentication(self):
        """Unauthenticated request must return 401."""
        res = self.req("GET", "/api/flags/queue")
        self.assertEqual(res.status, 401)

    def test_flags_queue_refuses_bhw_role(self):
        """BHW must not access the RHU queue (403)."""
        res = self.req("GET", "/api/flags/queue", token=self.tokens["bhw"])
        self.assertEqual(res.status, 403)

    def test_flags_queue_allowed_roles(self):
        """rhu_admin, midwife and physician must all receive 200."""
        for role in ("rhu_admin", "midwife", "physician"):
            res = self.req("GET", "/api/flags/queue", token=self.tokens[role])
            self.assertEqual(res.status, 200, f"expected 200 for role '{role}', got {res.status}")

    def test_flags_queue_response_contains_review_label(self):
        """The response must include the top-level review_label string."""
        res = self.req("GET", "/api/flags/queue", token=self.tokens["rhu_admin"])
        self.assertEqual(res.status, 200)
        self.assertIn("review_label", res.data)
        self.assertEqual(
            res.data["review_label"],
            "Requires review by a qualified health professional.",
        )

    def test_every_open_flag_in_queue_carries_review_label(self):
        """Each flag item in the flags list must carry the review_label field."""
        self._seed_open_flag(self.tokens["rhu_admin"])
        res = self.req("GET", "/api/flags/queue", token=self.tokens["rhu_admin"])
        self.assertEqual(res.status, 200)
        self.assertIn("flags", res.data)
        self.assertTrue(len(res.data["flags"]) > 0, "Expected at least one open flag in the queue")
        for flag in res.data["flags"]:
            self.assertIn("review_label", flag,
                          f"Flag id={flag.get('id')} is missing review_label")
            self.assertEqual(
                flag["review_label"],
                "Requires review by a qualified health professional.",
                f"Flag id={flag.get('id')} has wrong review_label value",
            )

    def test_flags_queue_only_shows_open_flags(self):
        """Flags with status 'reviewed' or 'dismissed' must not appear in the queue."""
        # Insert a 'reviewed' flag directly
        user_id = self.req("GET", "/api/me", token=self.tokens["rhu_admin"]).data["user"]["id"]
        # We need a valid mother_id; reuse one already in the DB
        list_res = self.req("GET", "/api/mothers", token=self.tokens["rhu_admin"])
        self.assertEqual(list_res.status, 200)
        self.assertTrue(len(list_res.data["mothers"]) > 0, "Need at least one mother to run this test")
        mother_id = list_res.data["mothers"][0]["id"]

        self.db.execute(
            """INSERT INTO risk_flags (mother_id, observation, raised_by, status, created_by)
               VALUES (?, 'Reviewed flag – should not appear', ?, 'reviewed', ?)""",
            (mother_id, user_id, user_id),
        )
        self.db.execute(
            """INSERT INTO risk_flags (mother_id, observation, raised_by, status, created_by)
               VALUES (?, 'Dismissed flag – should not appear', ?, 'dismissed', ?)""",
            (mother_id, user_id, user_id),
        )

        res = self.req("GET", "/api/flags/queue", token=self.tokens["rhu_admin"])
        self.assertEqual(res.status, 200)
        for flag in res.data["flags"]:
            self.assertEqual(flag["status"], "open",
                             f"Non-open flag (status={flag['status']}) appeared in the queue")

    def test_flags_queue_ordered_oldest_first(self):
        """Flags must be ordered by raised_at ascending (oldest flag first)."""
        user_id = self.req("GET", "/api/me", token=self.tokens["rhu_admin"]).data["user"]["id"]
        list_res = self.req("GET", "/api/mothers", token=self.tokens["rhu_admin"])
        mother_id = list_res.data["mothers"][0]["id"]

        # Insert two flags with known timestamps
        self.db.execute(
            """INSERT INTO risk_flags (mother_id, observation, raised_by, raised_at, status, created_by)
               VALUES (?, 'Older flag', ?, '2025-01-01 08:00:00', 'open', ?)""",
            (mother_id, user_id, user_id),
        )
        self.db.execute(
            """INSERT INTO risk_flags (mother_id, observation, raised_by, raised_at, status, created_by)
               VALUES (?, 'Newer flag', ?, '2025-06-01 08:00:00', 'open', ?)""",
            (mother_id, user_id, user_id),
        )

        res = self.req("GET", "/api/flags/queue", token=self.tokens["rhu_admin"])
        self.assertEqual(res.status, 200)
        raised_times = [f["raised_at"] for f in res.data["flags"]]
        self.assertEqual(raised_times, sorted(raised_times),
                         "Flags in the queue must be ordered oldest first (ascending raised_at)")


if __name__ == "__main__":
    unittest.main()
