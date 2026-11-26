"""HTTP app: static files, a JSON API, login sessions, role checks and an audit log.

The app takes a plain request (method, path, headers, body bytes) and returns
(status, content type, body bytes, extra headers), so the server and the tests share it.
"""
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import audit
import caretaker
import checklists as checklists_module
import db as patient_db
import mothers as mothers_module
import notes
import risk as risk_module
import referrals as referrals_module
import reminders as reminders_module
import sync as sync_module
import sessions
import users
import visit_records as vr
import visits

ROOT = Path(__file__).resolve().parent.parent
BLOCKED_DIRS = {"server", "data", "tests", ".git", ".vscode", "node_modules", ".claude"}
CARE_TEAM = ["provider", "nurse", "physician", "midwife", "bhw"]
ADMIN_ROLES = ["admin", "sysadmin", "rhu_admin"]

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}
JSON_TYPE = "application/json; charset=utf-8"
MAX_BODY_BYTES = 1_000_000


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Route:
    def __init__(self, method, path, roles, handler):
        self.method = method
        self.path = path
        self.roles = roles  # None = anyone
        self.handler = handler


def is_allowed_origin(origin):
    """Pages opened from a file send the origin "null"; the site itself runs on localhost."""
    return origin == "null" or re.fullmatch(r"https?://(localhost|127\.0\.0\.1)(:\d+)?", origin) is not None


def to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def json_response(status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return status, JSON_TYPE, body, {"Cache-Control": "no-store"}


def cors_headers(headers):
    """Lets pages opened from a file, or from localhost, read the response."""
    origin = headers.get("origin", "")
    if not origin or not is_allowed_origin(origin):
        return {}
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
        "Vary": "Origin",
    }


def error_response(status, message):
    return json_response(status, {"error": message})


class App:
    def __init__(self, db, log=print):
        self.db = db
        self.log = log
        self.routes = self._build_routes()

    def audit(self, ctx, action, detail=""):
        audit.record_audit(self.db, ctx["user"], action, detail)

    # ── Routes ─────────────────────────────────────────────

    def _build_routes(self):
        db = self.db
        log = self.log
        app = self

        def register(ctx):
            result = users.register_patient(db, ctx["body"])
            if "user" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            audit.record_audit(db, {"email": result["user"]["email"], "role": result["user"]["role"]}, "register")
            return 201, {"user": result["user"]}

        def login(ctx):
            body = ctx["body"]
            user = users.authenticate_user(db, body.get("email"), body.get("password"))
            if not user:
                audit.record_audit(db, {"email": str(body.get("email") or ""), "role": "unknown"}, "login_failed")
                raise HttpError(401, "Email or password is incorrect.")
            if user["status"] == "suspended":
                audit.record_audit(db, user, "login_suspended")
                raise HttpError(403, "This account is suspended. Contact the administrator.")
            audit.record_audit(db, user, "login")
            return 200, {"user": user, "token": sessions.create_session(db, user)}

        def logout(ctx):
            if ctx["user"]:
                app.audit(ctx, "logout")
            sessions.delete_session(db, ctx["token"])
            return 200, {"ok": True}

        def me(ctx):
            return 200, {"user": ctx["user"]}

        def password_reset(ctx):
            result = users.request_password_reset(db, ctx["body"].get("email"), log)
            audit.record_audit(db, None, "password_reset_requested", str(ctx["body"].get("email") or ""))
            return 200, result

        def password_reset_confirm(ctx):
            result = users.confirm_password_reset(db, ctx["body"])
            if not result.get("ok"):
                raise HttpError(result.get("status", 400), result["error"])
            audit.record_audit(db, None, "password_reset_completed")
            return 200, {"ok": True}

        def patients_list(ctx):
            return 200, {"patients": patient_db.list_patients(db)}

        def patient_get(ctx):
            patient = patient_db.get_patient(db, ctx["params"]["id"])
            if not patient:
                raise HttpError(404, "Patient not found")
            app.audit(ctx, "patient_view", patient["id"])
            return 200, {"patient": patient}

        def patient_update(ctx):
            patient = patient_db.update_patient(db, ctx["params"]["id"], ctx["body"])
            if not patient:
                raise HttpError(404, "Patient not found")
            app.audit(ctx, "patient_update", patient["id"])
            return 200, {"patient": patient}

        def visits_list(ctx):
            return 200, {"visits": visits.list_visits(db, date_from=ctx["query"].get("from"), date_to=ctx["query"].get("to"))}

        def visit_create(ctx):
            result = visits.create_visit(db, ctx["user"], ctx["body"])
            if "visit" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            v = result["visit"]
            app.audit(ctx, "visit_created", f"{v['patient_name']} {v['starts_on']} {v['starts_at']}")
            return 201, {"visit": v}

        def visit_update(ctx):
            visit_id = to_int(ctx["params"]["id"])
            visit = visits.update_visit(db, visit_id, ctx["body"]) if visit_id is not None else None
            if not visit:
                raise HttpError(404, "Visit not found")
            app.audit(ctx, "visit_updated", f"{visit['id']} {visit['starts_on']} {visit['status']}")
            return 200, {"visit": visit}

        def notes_list(ctx):
            return 200, {"notes": notes.list_notes(db)}

        def note_create(ctx):
            result = notes.add_note(db, ctx["user"], ctx["body"].get("body"))
            if "note" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "note_added", result["note"]["id"])
            return 201, {"note": result["note"]}

        def message_create(ctx):
            result = caretaker.send_care_message(db, ctx["user"], ctx["body"])
            if "message" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "message_sent", ctx["body"].get("patientId"))
            return 201, {"message": result["message"]}

        def caretaker_view(ctx):
            workspace = caretaker.get_caretaker_workspace(db, caretaker.CARETAKER_PATIENT_ID)
            if not workspace:
                raise HttpError(404, "Patient not found")
            app.audit(ctx, "caretaker_view", caretaker.CARETAKER_PATIENT_ID)
            return 200, workspace

        def task_update(ctx):
            if ctx["body"].get("done") is not True and ctx["body"].get("done") is not False:
                raise HttpError(400, '"done" must be true or false')
            task = caretaker.set_task_done(db, to_int(ctx["params"]["id"]), ctx["body"]["done"])
            if not task:
                raise HttpError(404, "Task not found")
            app.audit(ctx, "task_updated", f"{task['id']} done={str(task['done']).lower()}")
            return 200, {"task": task}

        def care_message_update(ctx):
            if ctx["body"].get("read") is not True:
                raise HttpError(400, '"read" must be true')
            if not caretaker.mark_message_read(db, to_int(ctx["params"]["id"])):
                raise HttpError(404, "Message not found")
            return 200, {"ok": True}

        def accounts_list(ctx):
            return 200, {"accounts": users.list_accounts(db)}

        def account_invite(ctx):
            result = users.invite_account(db, ctx["body"])
            if "account" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            a = result["account"]
            app.audit(ctx, "account_invited", f"{a['email']} as {a['role']}")
            return 201, {"account": a}

        def account_status(ctx):
            status = ctx["body"].get("status")
            if status not in ("active", "suspended"):
                raise HttpError(400, "Status must be active or suspended.")
            account_row_id = to_int(ctx["params"]["id"])
            if account_row_id == ctx["user"]["id"]:
                raise HttpError(400, "You cannot change the status of your own account.")
            account = users.set_account_status(db, account_row_id, status) if account_row_id is not None else None
            if not account:
                raise HttpError(404, "Account not found")
            app.audit(ctx, "account_suspended" if status == "suspended" else "account_activated", account["email"])
            return 200, {"account": account}

        def audit_list(ctx):
            return 200, {"events": audit.list_audit(db)}

        # ── Mothers (BHW-RHU Phase 2) ──────────────────────────

        def mothers_list(ctx):
            vr.mark_overdue_visits(db)
            result = mothers_module.list_mothers(db, ctx["user"])
            app.audit(ctx, "mothers_list")
            return 200, {"mothers": result}

        def mother_create(ctx):
            result = mothers_module.create_mother(db, ctx["user"], ctx["body"])
            if "mother" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "mother_created", str(result["mother"]["id"]))
            return 201, {"mother": result["mother"]}

        def mother_get(ctx):
            mother_id = to_int(ctx["params"]["id"])
            mother = mothers_module.get_mother(db, mother_id) if mother_id is not None else None
            if not mother:
                raise HttpError(404, "Mother not found.")
            app.audit(ctx, "mother_view", str(mother_id))
            return 200, {"mother": mother}

        def mother_history_create(ctx):
            mother_id = to_int(ctx["params"]["id"])
            result = mothers_module.add_pregnancy_history(db, ctx["user"], mother_id, ctx["body"])
            if "history" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "pregnancy_history_added", str(mother_id))
            return 201, {"history": result["history"]}

        def mother_history_list(ctx):
            mother_id = to_int(ctx["params"]["id"])
            if mother_id is None or not mothers_module.get_mother(db, mother_id):
                raise HttpError(404, "Mother not found.")
            app.audit(ctx, "mother_view", str(mother_id))
            return 200, {"history": mothers_module.list_pregnancy_history(db, mother_id)}

        # All roles that may read mother records
        MOTHERS_ROLES       = [*mothers_module.BHW_ROLES, *mothers_module.CLINICAL_ROLES, *mothers_module.RHU_ROLES]
        # Only these roles may register (create) a new mother record (physician/nurse can read but not register)
        MOTHERS_WRITE_ROLES = list(mothers_module.MOTHERS_WRITE_ROLES)

        # ── Visit records (Phase 3) ─────────────────────────────

        def prenatal_list(ctx):
            mid = to_int(ctx["params"]["id"])
            vr.mark_overdue_visits(db)
            return 200, {"visits": vr.list_prenatal(db, mid)}

        def prenatal_create(ctx):
            mid = to_int(ctx["params"]["id"])
            result = vr.create_prenatal(db, ctx["user"], mid, ctx["body"])
            if "visit" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "prenatal_scheduled", str(result["visit"]["id"]))
            return 201, {"visit": result["visit"]}

        def prenatal_update(ctx):
            vid = to_int(ctx["params"]["vid"])
            result = vr.update_prenatal(db, ctx["user"], vid, ctx["body"])
            if "visit" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "prenatal_updated", str(vid))
            return 200, {"visit": result["visit"]}

        def postnatal_list(ctx):
            mid = to_int(ctx["params"]["id"])
            vr.mark_overdue_visits(db)
            return 200, {"visits": vr.list_postnatal(db, mid)}

        def postnatal_create(ctx):
            mid = to_int(ctx["params"]["id"])
            result = vr.create_postnatal(db, ctx["user"], mid, ctx["body"])
            if "visit" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "postnatal_scheduled", str(result["visit"]["id"]))
            return 201, {"visit": result["visit"]}

        def postnatal_update(ctx):
            vid = to_int(ctx["params"]["vid"])
            result = vr.update_postnatal(db, ctx["user"], vid, ctx["body"])
            if "visit" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "postnatal_updated", str(vid))
            return 200, {"visit": result["visit"]}

        def home_visit_list(ctx):
            mid = to_int(ctx["params"]["id"])
            vr.mark_overdue_visits(db)
            return 200, {"visits": vr.list_home_visits(db, mid)}

        def home_visit_create(ctx):
            mid = to_int(ctx["params"]["id"])
            result = vr.create_home_visit(db, ctx["user"], mid, ctx["body"])
            if "visit" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "home_visit_planned", str(result["visit"]["id"]))
            return 201, {"visit": result["visit"]}

        def home_visit_update(ctx):
            vid = to_int(ctx["params"]["vid"])
            result = vr.update_home_visit(db, ctx["user"], vid, ctx["body"])
            if "visit" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "home_visit_updated", str(vid))
            return 200, {"visit": result["visit"]}

        def bhw_schedule_get(ctx):
            """Due-today / overdue / missed list for the signed-in BHW.
            Calling this also runs mark_overdue_visits so the data is fresh."""
            user = ctx["user"]
            schedule = vr.bhw_schedule(db, user["id"])
            return 200, schedule

        # ── Flat scheduling endpoints (task 3.2) ───────────────────────────
        # POST /api/prenatal-visits  — mother_id in body
        # PATCH /api/prenatal-visits/:id/status — status-only update by visit id

        def flat_prenatal_create(ctx):
            mother_id = to_int(ctx["body"].get("motherId") or ctx["body"].get("mother_id"))
            if mother_id is None:
                raise HttpError(400, "motherId is required.")
            result = vr.create_prenatal(db, ctx["user"], mother_id, ctx["body"])
            if "visit" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "prenatal_scheduled", str(result["visit"]["id"]))
            return 201, {"visit": result["visit"]}

        def flat_prenatal_status(ctx):
            vid = to_int(ctx["params"]["id"])
            new_status = ctx["body"].get("status") if ctx["body"] else None
            if not new_status:
                raise HttpError(400, "status is required.")
            result = vr.update_prenatal(db, ctx["user"], vid, {"status": new_status})
            if "visit" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "prenatal_status_updated", f"{vid} → {new_status}")
            return 200, {"visit": result["visit"]}

        def flat_postnatal_create(ctx):
            mother_id = to_int(ctx["body"].get("motherId") or ctx["body"].get("mother_id"))
            if mother_id is None:
                raise HttpError(400, "motherId is required.")
            result = vr.create_postnatal(db, ctx["user"], mother_id, ctx["body"])
            if "visit" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "postnatal_scheduled", str(result["visit"]["id"]))
            return 201, {"visit": result["visit"]}

        def flat_postnatal_status(ctx):
            vid = to_int(ctx["params"]["id"])
            new_status = ctx["body"].get("status") if ctx["body"] else None
            if not new_status:
                raise HttpError(400, "status is required.")
            result = vr.update_postnatal(db, ctx["user"], vid, {"status": new_status})
            if "visit" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "postnatal_status_updated", f"{vid} → {new_status}")
            return 200, {"visit": result["visit"]}

        def flat_home_visit_create(ctx):
            mother_id = to_int(ctx["body"].get("motherId") or ctx["body"].get("mother_id"))
            if mother_id is None:
                raise HttpError(400, "motherId is required.")
            result = vr.create_home_visit(db, ctx["user"], mother_id, ctx["body"])
            if "visit" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "home_visit_planned", str(result["visit"]["id"]))
            return 201, {"visit": result["visit"]}

        def flat_home_visit_status(ctx):
            vid = to_int(ctx["params"]["id"])
            new_status = ctx["body"].get("status") if ctx["body"] else None
            if not new_status:
                raise HttpError(400, "status is required.")
            result = vr.update_home_visit(db, ctx["user"], vid, {"status": new_status})
            if "visit" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "home_visit_status_updated", f"{vid} → {new_status}")
            return 200, {"visit": result["visit"]}

        VISIT_ROLES = [*mothers_module.BHW_ROLES, *mothers_module.CLINICAL_ROLES, *mothers_module.RHU_ROLES]
        BHW_ONLY    = list(mothers_module.BHW_ROLES)
        BHW_MID_ROLES = list({*mothers_module.BHW_ROLES, "midwife"})

        # ── Checklists (Phase 4) ───────────────────────────────────────────

        def checklist_create(ctx):
            result = checklists_module.create_checklist(db, ctx["user"], ctx["body"])
            if "checklist" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "checklist_created", str(result["checklist"]["id"]))
            return 201, {"checklist": result["checklist"]}

        def checklist_get(ctx):
            checklist_id = to_int(ctx["params"]["id"])
            if checklist_id is None:
                raise HttpError(404, "Checklist not found.")
            result = checklists_module.get_checklist(db, checklist_id)
            if "checklist" not in result:
                raise HttpError(result.get("status", 404), result["error"])
            return 200, {"checklist": result["checklist"]}

        CHECKLIST_CREATE_ROLES = list(checklists_module.CHECKLIST_CREATE_ROLES)
        CHECKLIST_READ_ROLES   = list(checklists_module.CHECKLIST_READ_ROLES)

        # ── Risk indicators (Phase 5) ──────────────────────────────────────────

        def indicator_create(ctx):
            result = risk_module.create_indicator(db, ctx["user"], ctx["body"])
            if "indicator" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "indicator_created", str(result["indicator"]["id"]))
            return 201, {"indicator": result["indicator"]}

        def indicators_list(ctx):
            return 200, {"indicators": risk_module.list_indicators(db)}

        def flags_queue(ctx):
            """Return every open risk flag for the RHU queue (task 5.4)."""
            flags = risk_module.list_open_flags(db)
            app.audit(ctx, "flags_queue_viewed")
            return 200, {
                "flags": flags,
                "review_label": risk_module.QUEUE_REVIEW_LABEL,
            }

        def referrals_overdue(ctx):
            """Return pending referrals that were not acknowledged within the protocol limit.

            Uses DEFAULT_OVERDUE_HOURS as the fallback when due_by is not set
            (task 6.4; task 0.3 tracks the open question on the confirmed protocol limit).
            """
            overdue = referrals_module.list_overdue_referrals(db)
            app.audit(ctx, "overdue_referrals_viewed", f"count={len(overdue)}")
            return 200, {"referrals": overdue}

        def observation_create(ctx):
            """POST /api/mothers/:id/observations — BHW or midwife records an observation.

            Matches the text against active indicators and raises a risk_flag with
            status='open'.  If no indicator matches, a free-text flag is created.
            A flag is NEVER auto-dismissed here; only an explicit reviewer action
            may change its status (task 5.3).
            """
            mother_id = to_int(ctx["params"]["id"])
            result = risk_module.record_observation(db, ctx["user"], mother_id, ctx["body"])
            if "flag" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "observation_recorded", str(result["flag"]["id"]))
            return 201, {"flag": result["flag"]}

        def mother_flags_list(ctx):
            """GET /api/mothers/:id/flags — list flags for a mother."""
            mother_id = to_int(ctx["params"]["id"])
            status_filter = ctx["query"].get("status")
            flags = risk_module.list_flags(db, mother_id=mother_id, status_filter=status_filter)
            return 200, {"flags": flags}

        # ── Follow-up list (Phase 6, task 6.5) ────────────────────────────────

        def follow_up_list(ctx):
            """GET /api/follow-up — mothers with at least one open (non-closed) referral.

            A mother stays on this list until every referral raised against her
            is closed (design.md section 6, task 6.5).
            """
            mothers_on_list = referrals_module.list_mothers_with_open_referrals(db, user=ctx["user"])
            return 200, {"followUp": mothers_on_list}

        FOLLOW_UP_ROLES = list(referrals_module.REFERRAL_WRITE_ROLES)

        INDICATOR_ADMIN_ROLES = list(risk_module.INDICATOR_ADMIN_ROLES)
        FLAG_QUEUE_ROLES      = list(risk_module.FLAG_QUEUE_ROLES)
        OBSERVATION_ROLES     = list({*mothers_module.BHW_ROLES, "midwife"})

        # ── Referrals (Phase 6) ────────────────────────────────────────────

        REFERRAL_WRITE_ROLES  = list(referrals_module.REFERRAL_WRITE_ROLES)
        REFERRAL_REVIEW_ROLES = list(referrals_module.REFERRAL_REVIEW_ROLES)
        REFERRAL_READ_ROLES   = list({*referrals_module.REFERRAL_WRITE_ROLES, *referrals_module.REFERRAL_REVIEW_ROLES})
        # Overdue referrals are shown on the RHU dashboard (task 6.4);
        # rhu_admin, physician and midwife may view them.
        REFERRAL_OVERDUE_ROLES = ["rhu_admin", "physician", "midwife"]

        def referral_create(ctx):
            result = referrals_module.create_referral(db, ctx["user"], ctx["body"])
            if "referral" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "referral_created", str(result["referral"]["id"]))
            return 201, {"referral": result["referral"]}

        def referrals_list(ctx):
            mother_id = to_int(ctx["query"].get("mother_id") or ctx["query"].get("motherId"))
            status_filter = ctx["query"].get("status")
            return 200, {"referrals": referrals_module.list_referrals(db, mother_id=mother_id, status=status_filter)}

        def referral_get(ctx):
            referral_id = to_int(ctx["params"]["id"])
            referral = referrals_module.get_referral(db, referral_id) if referral_id is not None else None
            if not referral:
                raise HttpError(404, "Referral not found.")
            events = referrals_module.list_events(db, referral_id)
            app.audit(ctx, "referral_viewed", str(referral_id))
            return 200, {"referral": referral, "events": events}

        def referral_status_update(ctx):
            referral_id = to_int(ctx["params"]["id"])
            if referral_id is None:
                raise HttpError(404, "Referral not found.")
            result = referrals_module.advance_referral_status(db, ctx["user"], referral_id, ctx["body"])
            if "referral" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "referral_status_updated",
                      f"{referral_id} → {result['referral']['status']}")
            return 200, {"referral": result["referral"], "event": result["event"]}

        # ── Reminders (Phase 7, task 7.1) ─────────────────────────────────────

        REMINDER_ROLES = list(reminders_module.REMINDER_ROLES)

        def reminders_list(ctx):
            """GET /api/reminders — list reminders for the signed-in user."""
            user = ctx["user"]
            user_id   = to_int(ctx["query"].get("user_id"))   or user["id"]
            mother_id = to_int(ctx["query"].get("mother_id"))
            channel   = ctx["query"].get("channel") or None
            result = reminders_module.list_reminders(
                db, user_id=user_id, mother_id=mother_id, channel=channel
            )
            return 200, {"reminders": result}

        def reminder_create(ctx):
            """POST /api/reminders — create a reminder."""
            result = reminders_module.create_reminder(db, ctx["body"])
            if "reminder" not in result:
                raise HttpError(result.get("status", 400), result["error"])
            app.audit(ctx, "reminder_created", str(result["reminder"]["id"]))
            return 201, {"reminder": result["reminder"]}

        # ── Offline sync (Phase 8, task 8.3) ──────────────────────────────────

        SYNC_ROLES = list(sync_module.SYNC_ROLES)

        def outbox_sync(ctx):
            """POST /api/sync — accept an ordered offline outbox from a BHW device.

            Body: {"outbox": [{client_id, entity, payload}, ...]}
            Returns: {"results": [{client_id, status, ...}]}

            Each item is processed idempotently: a client_id already present in
            sync_outbox is returned as {"status": "duplicate"} without being
            re-applied (design.md §5, task 8.3).
            """
            outbox = ctx["body"].get("outbox")
            if not isinstance(outbox, list):
                raise HttpError(400, '"outbox" must be a list.')
            results = sync_module.process_outbox(db, ctx["user"], outbox)
            app.audit(ctx, "sync_uploaded", f"items={len(outbox)}")
            return 200, {"results": results}

        return [
            Route("POST", "/api/register", None, register),
            Route("POST", "/api/login", None, login),
            Route("DELETE", "/api/session", None, logout),
            Route("GET", "/api/me", None, me),
            Route("POST", "/api/password-reset", None, password_reset),
            Route("POST", "/api/password-reset/confirm", None, password_reset_confirm),
            Route("GET", "/api/patients", CARE_TEAM, patients_list),
            Route("GET", "/api/patients/:id", CARE_TEAM, patient_get),
            Route("PATCH", "/api/patients/:id", CARE_TEAM, patient_update),
            Route("GET", "/api/visits", CARE_TEAM, visits_list),
            Route("POST", "/api/visits", CARE_TEAM, visit_create),
            Route("PATCH", "/api/visits/:id", CARE_TEAM, visit_update),
            Route("GET", "/api/notes", CARE_TEAM, notes_list),
            Route("POST", "/api/notes", CARE_TEAM, note_create),
            Route("POST", "/api/messages", CARE_TEAM, message_create),
            Route("GET", "/api/caretaker", ["caretaker", *CARE_TEAM], caretaker_view),
            Route("PATCH", "/api/caretaker/tasks/:id", ["caretaker"], task_update),
            Route("PATCH", "/api/caretaker/messages/:id", ["caretaker"], care_message_update),
            Route("GET", "/api/accounts", ADMIN_ROLES, accounts_list),
            Route("POST", "/api/accounts", ADMIN_ROLES, account_invite),
            Route("PATCH", "/api/accounts/:id", ADMIN_ROLES, account_status),
            Route("GET", "/api/audit", ADMIN_ROLES, audit_list),
            # Mothers (Phase 2)
            # GET: all clinical and admin roles may read mothers (role-scoped in list_mothers)
            # POST: only bhw/midwife/rhu_admin/sysadmin may register a new mother
            Route("GET",   "/api/mothers",                    MOTHERS_ROLES,       mothers_list),
            Route("POST",  "/api/mothers",                    MOTHERS_WRITE_ROLES, mother_create),
            Route("GET",   "/api/mothers/:id",                MOTHERS_ROLES,       mother_get),
            Route("POST",  "/api/mothers/:id/history",        MOTHERS_ROLES,       mother_history_create),
            Route("GET",   "/api/mothers/:id/history",        MOTHERS_ROLES,       mother_history_list),
            # Visits – prenatal (Phase 3)
            Route("GET",   "/api/mothers/:id/prenatal",       VISIT_ROLES,   prenatal_list),
            Route("POST",  "/api/mothers/:id/prenatal",       VISIT_ROLES,   prenatal_create),
            Route("PATCH", "/api/mothers/:id/prenatal/:vid",  VISIT_ROLES,   prenatal_update),
            # Visits – postnatal (Phase 3)
            Route("GET",   "/api/mothers/:id/postnatal",      VISIT_ROLES,   postnatal_list),
            Route("POST",  "/api/mothers/:id/postnatal",      VISIT_ROLES,   postnatal_create),
            Route("PATCH", "/api/mothers/:id/postnatal/:vid", VISIT_ROLES,   postnatal_update),
            # Visits – home (Phase 3)
            Route("GET",   "/api/mothers/:id/home-visits",        VISIT_ROLES, home_visit_list),
            Route("POST",  "/api/mothers/:id/home-visits",        VISIT_ROLES, home_visit_create),
            Route("PATCH", "/api/mothers/:id/home-visits/:vid",   VISIT_ROLES, home_visit_update),
            # BHW schedule dashboard (Phase 3)
            Route("GET",   "/api/bhw/schedule",               BHW_ONLY,     bhw_schedule_get),
            # Flat scheduling endpoints (task 3.2)
            Route("POST",  "/api/prenatal-visits",             VISIT_ROLES,     flat_prenatal_create),
            Route("PATCH", "/api/prenatal-visits/:id/status",  VISIT_ROLES,     flat_prenatal_status),
            Route("POST",  "/api/postnatal-visits",            VISIT_ROLES,     flat_postnatal_create),
            Route("PATCH", "/api/postnatal-visits/:id/status", VISIT_ROLES,     flat_postnatal_status),
            Route("POST",  "/api/home-visits",                 BHW_MID_ROLES,   flat_home_visit_create),
            Route("PATCH", "/api/home-visits/:id/status",      BHW_MID_ROLES,   flat_home_visit_status),
            # Checklists (Phase 4)
            Route("POST", "/api/checklists",     CHECKLIST_CREATE_ROLES, checklist_create),
            Route("GET",  "/api/checklists/:id", CHECKLIST_READ_ROLES,   checklist_get),
            # Risk indicators (Phase 5)
            Route("POST", "/api/indicators", INDICATOR_ADMIN_ROLES, indicator_create),
            Route("GET",  "/api/indicators", INDICATOR_ADMIN_ROLES, indicators_list),
            # Observations and flags (Phase 5, task 5.3)
            Route("POST", "/api/mothers/:id/observations", OBSERVATION_ROLES, observation_create),
            Route("GET",  "/api/mothers/:id/flags",        MOTHERS_ROLES,     mother_flags_list),
            # RHU open-flags queue (Phase 5, task 5.4)
            Route("GET",  "/api/flags/queue", FLAG_QUEUE_ROLES, flags_queue),
            # Referrals (Phase 6, task 6.1–6.2)
            Route("POST",  "/api/referrals",             REFERRAL_WRITE_ROLES,  referral_create),
            Route("GET",   "/api/referrals",             REFERRAL_READ_ROLES,   referrals_list),
            # Overdue referrals for RHU dashboard (Phase 6, task 6.4) — must precede /:id
            Route("GET",   "/api/referrals/overdue",     REFERRAL_OVERDUE_ROLES, referrals_overdue),
            Route("GET",   "/api/referrals/:id",         REFERRAL_READ_ROLES,   referral_get),
            Route("PATCH", "/api/referrals/:id/status",  REFERRAL_REVIEW_ROLES, referral_status_update),
            # Follow-up list (Phase 6, task 6.5)
            Route("GET",  "/api/follow-up", FOLLOW_UP_ROLES, follow_up_list),
            # Reminders (Phase 7, task 7.1)
            Route("GET",  "/api/reminders", REMINDER_ROLES, reminders_list),
            Route("POST", "/api/reminders", REMINDER_ROLES, reminder_create),
            # Offline sync (Phase 8, task 8.3)
            Route("POST", "/api/sync", SYNC_ROLES, outbox_sync),
        ]

    # ── Request handling ───────────────────────────────────

    def match_route(self, method, pathname):
        """Returns (route, params, path_matched)."""
        url_parts = [p for p in pathname.split("/") if p]
        path_matched = False
        for route in self.routes:
            route_parts = [p for p in route.path.split("/") if p]
            if len(route_parts) != len(url_parts):
                continue
            params = {}
            ok = True
            for route_part, url_part in zip(route_parts, url_parts):
                if route_part.startswith(":"):
                    params[route_part[1:]] = unquote(url_part)
                elif route_part != url_part:
                    ok = False
                    break
            if not ok:
                continue
            path_matched = True
            if route.method == method:
                return route, params, True
        return None, None, path_matched

    def handle_api(self, method, pathname, query, headers, raw_body):
        route, params, path_matched = self.match_route(method, pathname)
        if not route:
            return error_response(405, "Method not allowed") if path_matched else error_response(404, "Not found")

        header = headers.get("authorization", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        user = sessions.get_session_user(self.db, token)
        body = {} if method in ("GET", "DELETE") else parse_body(raw_body)
        ctx = {"user": user, "token": token, "params": params, "body": body, "query": query}
        if route.roles is not None:
            if not user:
                raise HttpError(401, "Please sign in first.")
            if user["role"] not in route.roles:
                raise HttpError(403, "Your account cannot do this.")
        status, payload = route.handler(ctx)
        return json_response(status, payload)

    def serve_static(self, pathname):
        relative = unquote(pathname)
        if relative in ("/", ""):
            relative = "/index.html"
        target = (ROOT / relative.lstrip("/")).resolve()
        try:
            parts = target.relative_to(ROOT).parts
        except ValueError:
            # Stay inside the project folder.
            return 404, "text/plain; charset=utf-8", b"Not found", {}
        if (parts and parts[0] in BLOCKED_DIRS) or not target.is_file():
            # Never expose the database, server code or tests.
            return 404, "text/plain; charset=utf-8", b"Not found", {}
        content_type = MIME.get(target.suffix.lower(), "application/octet-stream")
        return 200, content_type, target.read_bytes(), {}

    def handle(self, method, raw_path, headers, raw_body=b""):
        """headers: dict with lower-case keys. Returns (status, content type, body bytes, extra headers)."""
        try:
            parts = urlsplit(raw_path)
            pathname = parts.path
            query = {key: values[0] for key, values in parse_qs(parts.query).items()}
            cors = cors_headers(headers)
            if method == "OPTIONS":
                return 204, None, b"", cors
            if pathname.startswith("/api/"):
                status, content_type, body, extra = self.handle_api(method, pathname, query, headers, raw_body)
            elif method in ("GET", "HEAD"):
                status, content_type, body, extra = self.serve_static(pathname)
            else:
                return 405, "text/plain; charset=utf-8", b"Method not allowed", cors
            return status, content_type, body, {**cors, **extra}
        except HttpError as error:
            status, content_type, body, _ = error_response(error.status, error.message)
            return status, content_type, body, cors_headers(headers)  # errors need CORS too, or the browser hides them
        except Exception as error:  # keep the server running; report a generic error
            self.log("[server error]", repr(error))
            status, content_type, body, _ = error_response(500, "Server error")
            return status, content_type, body, cors_headers(headers)


def parse_body(raw_body):
    if not raw_body:
        return {}
    try:
        value = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HttpError(400, "Invalid JSON body")
    return value if isinstance(value, dict) else {}


def create_app(db, log=print):
    return App(db, log)
