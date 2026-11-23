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
import db as patient_db
import notes
import sessions
import users
import visits

ROOT = Path(__file__).resolve().parent.parent
BLOCKED_DIRS = {"server", "data", "tests", ".git", ".vscode", "node_modules", ".claude"}
CARE_TEAM = ["provider", "nurse"]

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
            audit.record_audit(db, {"email": result["user"]["email"], "role": "patient"}, "register")
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
            Route("GET", "/api/accounts", ["admin"], accounts_list),
            Route("POST", "/api/accounts", ["admin"], account_invite),
            Route("PATCH", "/api/accounts/:id", ["admin"], account_status),
            Route("GET", "/api/audit", ["admin"], audit_list),
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
            origin = headers.get("origin", "")
            cors = {}
            if origin and is_allowed_origin(origin):
                cors = {
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Headers": "Content-Type, Authorization",
                    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
                    "Vary": "Origin",
                }
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
            status, content_type, body, extra = error_response(error.status, error.message)
            return status, content_type, body, {}
        except Exception as error:  # keep the server running; report a generic error
            self.log("[server error]", repr(error))
            status, content_type, body, extra = error_response(500, "Server error")
            return status, content_type, body, {}


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
