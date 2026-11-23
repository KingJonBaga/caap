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
            "caretaker": cls.client.sign_in("d.cruz@mhms.example", STAFF_PASSWORD),
            "nurse": cls.client.sign_in("l.park@mhms.example", STAFF_PASSWORD),
            "provider": cls.client.sign_in("a.rahman@mhms.example", STAFF_PASSWORD),
            "admin": cls.client.sign_in("admin@example.com", STAFF_PASSWORD),
        }

    def req(self, method, path, **kwargs):
        return self.client.request(method, path, **kwargs)

    def reset_code_for(self, email, before):
        line = next(text for text in self.log_lines[before:] if "Code for " + email in text)
        return re.search(r": ([A-F0-9]{8})", line).group(1)

    # ── Sign-in and sessions ────────────────────────────────

    def test_seeded_accounts_sign_in_with_their_roles(self):
        res = self.req("POST", "/api/login", body={"email": "d.cruz@mhms.example", "password": STAFF_PASSWORD})
        self.assertEqual(res.data["user"]["role"], "caretaker")
        self.assertGreaterEqual(len(res.data["token"]), 32)
        self.assertNotIn("password_hash", res.data["user"], "password hash must never be sent")

    def test_wrong_password_and_unknown_email_are_rejected_the_same_way(self):
        wrong = self.req("POST", "/api/login", body={"email": "l.park@mhms.example", "password": "nope-nope"})
        unknown = self.req("POST", "/api/login", body={"email": "nobody@example.com", "password": STAFF_PASSWORD})
        self.assertEqual(wrong.status, 401)
        self.assertEqual(unknown.status, 401)
        self.assertEqual(wrong.data["error"], unknown.data["error"])

    def test_requests_without_a_valid_token_get_401(self):
        self.assertEqual(self.req("GET", "/api/caretaker").status, 401)
        self.assertEqual(self.req("GET", "/api/caretaker", token="not-a-real-token").status, 401)

    def test_logout_ends_the_session(self):
        token = self.client.sign_in("admin@example.com", STAFF_PASSWORD)
        self.assertEqual(self.req("GET", "/api/me", token=token).data["user"]["role"], "admin")
        self.req("DELETE", "/api/session", token=token)
        self.assertEqual(self.req("GET", "/api/caretaker", token=token).status, 401)

    # ── Role rules ──────────────────────────────────────────

    def test_caretakers_cannot_read_patient_records(self):
        self.assertEqual(self.req("GET", "/api/patients", token=self.tokens["caretaker"]).status, 403)
        self.assertEqual(self.req("GET", "/api/patients/MH-2025-0148", token=self.tokens["caretaker"]).status, 403)

    def test_nurses_and_doctors_can_read_patient_records(self):
        self.assertEqual(self.req("GET", "/api/patients", token=self.tokens["nurse"]).status, 200)
        self.assertEqual(self.req("GET", "/api/patients/MH-2025-0148", token=self.tokens["provider"]).status, 200)

    def test_only_admins_can_read_the_audit_log(self):
        self.assertEqual(self.req("GET", "/api/audit", token=self.tokens["nurse"]).status, 403)
        self.assertEqual(self.req("GET", "/api/audit", token=self.tokens["caretaker"]).status, 403)
        res = self.req("GET", "/api/audit", token=self.tokens["admin"])
        self.assertEqual(res.status, 200)
        self.assertTrue(any(event["action"] == "login" for event in res.data["events"]))

    def test_caretakers_can_tick_their_tasks_but_nurses_cannot(self):
        task = self.req("GET", "/api/caretaker", token=self.tokens["caretaker"]).data["tasks"][0]
        denied = self.req("PATCH", f"/api/caretaker/tasks/{task['id']}", token=self.tokens["nurse"], body={"done": True})
        self.assertEqual(denied.status, 403)
        saved = self.req("PATCH", f"/api/caretaker/tasks/{task['id']}", token=self.tokens["caretaker"], body={"done": True})
        self.assertEqual(saved.status, 200)
        self.assertIs(saved.data["task"]["done"], True)
        self.req("PATCH", f"/api/caretaker/tasks/{task['id']}", token=self.tokens["caretaker"], body={"done": False})

    # ── Messages and visits ─────────────────────────────────

    def test_a_nurse_can_message_the_caretaker_and_the_caretaker_sees_it(self):
        sent = self.req("POST", "/api/messages", token=self.tokens["nurse"],
                        body={"patientId": "MH-2025-0148", "body": "Reminder: bring your ID to the 3 Dec visit."})
        self.assertEqual(sent.status, 201)
        self.assertEqual(sent.data["message"]["sender"], "Nurse L. Park")
        workspace = self.req("GET", "/api/caretaker", token=self.tokens["caretaker"])
        self.assertTrue(any("bring your ID" in m["body"] for m in workspace.data["messages"]))

    def test_caretakers_cannot_send_messages_and_empty_messages_are_refused(self):
        denied = self.req("POST", "/api/messages", token=self.tokens["caretaker"],
                          body={"patientId": "MH-2025-0148", "body": "hi"})
        self.assertEqual(denied.status, 403)
        empty = self.req("POST", "/api/messages", token=self.tokens["nurse"],
                         body={"patientId": "MH-2025-0148", "body": "   "})
        self.assertEqual(empty.status, 400)

    def test_a_nurse_can_add_an_appointment_and_it_reaches_the_caretaker_page(self):
        created = self.req("POST", "/api/visits", token=self.tokens["nurse"], body={
            "patientId": "MH-2025-0148", "patientName": "Maria Santos", "startsOn": "2026-12-17",
            "startsAt": "11:00", "title": "Blood pressure check", "room": "Room 2",
        })
        self.assertEqual(created.status, 201)
        workspace = self.req("GET", "/api/caretaker", token=self.tokens["caretaker"])
        self.assertTrue(any(v["title"] == "Blood pressure check" for v in workspace.data["appointments"]))

    def test_appointments_need_a_date_time_and_visit_type(self):
        res = self.req("POST", "/api/visits", token=self.tokens["nurse"],
                       body={"patientName": "Test Patient", "startsOn": "", "startsAt": "09:00", "title": "Check"})
        self.assertEqual(res.status, 400)

    def test_moving_a_visit_to_another_day_in_the_week_saves_the_change(self):
        visits = self.req("GET", "/api/visits?from=2026-11-30&to=2026-12-04", token=self.tokens["nurse"])
        first = visits.data["visits"][0]
        moved = self.req("PATCH", f"/api/visits/{first['id']}", token=self.tokens["nurse"], body={"startsOn": "2026-12-01"})
        self.assertEqual(moved.status, 200)
        self.assertEqual(moved.data["visit"]["starts_on"], "2026-12-01")

    def test_caseload_notes_are_saved_and_listed(self):
        added = self.req("POST", "/api/notes", token=self.tokens["provider"],
                         body={"body": "Call Hannah about her week-4 screening."})
        self.assertEqual(added.status, 201)
        listed = self.req("GET", "/api/notes", token=self.tokens["nurse"])
        self.assertTrue(any("week-4 screening" in n["body"] for n in listed.data["notes"]))

    # ── Register and password reset ─────────────────────────

    def test_a_new_patient_can_register_and_then_sign_in(self):
        email = "new.patient@example.com"
        created = self.req("POST", "/api/register", body={"name": "New Patient", "email": email, "password": "longenough1"})
        self.assertEqual(created.status, 201)
        self.assertEqual(created.data["user"]["role"], "patient")
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
        res = self.req("GET", "/api/accounts", token=self.tokens["admin"])
        self.assertEqual(res.status, 200)
        self.assertTrue(any(a["email"] == "d.cruz@mhms.example" and a["status"] == "active" for a in res.data["accounts"]))
        self.assertTrue(all(a["accountId"].startswith("ACC-") for a in res.data["accounts"]))
        self.assertEqual(self.req("GET", "/api/accounts", token=self.tokens["nurse"]).status, 403)

    def test_an_invited_account_has_no_known_password_until_the_person_sets_one(self):
        invited = self.req("POST", "/api/accounts", token=self.tokens["admin"],
                           body={"name": "Invited Nurse", "email": "invited.nurse@example.com", "role": "nurse"})
        self.assertEqual(invited.status, 201)
        self.assertEqual(invited.data["account"]["role"], "nurse")
        self.assertNotIn("password_hash", invited.data["account"])
        self.assertEqual(self.req("POST", "/api/login", body={"email": "invited.nurse@example.com", "password": "guess-guess"}).status, 401)

        before = len(self.log_lines)
        self.req("POST", "/api/password-reset", body={"email": "invited.nurse@example.com"})
        code = self.reset_code_for("invited.nurse@example.com", before)
        self.req("POST", "/api/password-reset/confirm", body={"code": code, "password": "invitednurse1"})
        self.assertEqual(self.req("POST", "/api/login", body={"email": "invited.nurse@example.com", "password": "invitednurse1"}).status, 200)

    def test_suspending_an_account_blocks_sign_in_and_ends_its_sessions_and_activating_restores_it(self):
        accounts = self.req("GET", "/api/accounts", token=self.tokens["admin"])
        target = next(a for a in accounts.data["accounts"] if a["email"] == "invited.nurse@example.com")
        nurse_token = self.client.sign_in("invited.nurse@example.com", "invitednurse1")

        suspended = self.req("PATCH", f"/api/accounts/{target['id']}", token=self.tokens["admin"], body={"status": "suspended"})
        self.assertEqual(suspended.status, 200)
        self.assertEqual(suspended.data["account"]["status"], "suspended")
        self.assertEqual(self.req("GET", "/api/visits", token=nurse_token).status, 401, "open session is ended")

        blocked = self.req("POST", "/api/login", body={"email": "invited.nurse@example.com", "password": "invitednurse1"})
        self.assertEqual(blocked.status, 403)
        self.assertIn("suspended", blocked.data["error"])

        self.req("PATCH", f"/api/accounts/{target['id']}", token=self.tokens["admin"], body={"status": "active"})
        self.assertEqual(self.req("POST", "/api/login", body={"email": "invited.nurse@example.com", "password": "invitednurse1"}).status, 200)

    def test_admins_cannot_suspend_themselves_and_bad_statuses_or_roles_are_refused(self):
        me = self.req("GET", "/api/me", token=self.tokens["admin"])
        accounts = self.req("GET", "/api/accounts", token=self.tokens["admin"])
        admin_row = next(a for a in accounts.data["accounts"] if a["email"] == "admin@example.com")
        self.assertEqual(me.data["user"]["id"], admin_row["id"])
        self.assertEqual(self.req("PATCH", f"/api/accounts/{admin_row['id']}", token=self.tokens["admin"], body={"status": "suspended"}).status, 400)
        self.assertEqual(self.req("PATCH", f"/api/accounts/{admin_row['id']}", token=self.tokens["admin"], body={"status": "deleted"}).status, 400)
        self.assertEqual(self.req("POST", "/api/accounts", token=self.tokens["admin"],
                                  body={"name": "X Y", "email": "x.y@example.com", "role": "superuser"}).status, 400)
        self.assertEqual(self.req("POST", "/api/accounts", token=self.tokens["admin"],
                                  body={"name": "Dup", "email": "d.cruz@mhms.example", "role": "nurse"}).status, 409)

    def test_the_site_itself_is_served(self):
        res = self.req("GET", "/index.html")
        self.assertEqual(res.status, 200)
        self.assertIn("text/html", res.content_type)


if __name__ == "__main__":
    unittest.main()
