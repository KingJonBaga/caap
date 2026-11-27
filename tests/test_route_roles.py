"""Task 11.3: every protected route refuses people without a session (401)
and people whose role is not allowed (403).

Uses an in-memory database, so data/caap.db is never touched."""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import users  # noqa: E402
from app import create_app  # noqa: E402
from db import open_database  # noqa: E402

PASSWORD = "test-only-route-pass"


def fill_path(pattern):
    """Replaces each :name part of a route path with a harmless value."""
    return re.sub(r":[A-Za-z_]+", "1", pattern)


class RouteRoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = open_database(":memory:", shared_password=PASSWORD)
        cls.app = create_app(cls.db, log=lambda *_: None)
        # One account for every role the system knows about, so each route can be tested.
        cls.tokens = {}
        for index, role in enumerate(users.ROLES):
            email = f"route.test.{index}@example.com"
            result = users.invite_account(cls.db, {"name": f"Route {role}", "email": email, "role": role})
            assert "account" in result, result
            users.set_account_status(cls.db, result["account"]["id"], "active")
            cls.db.execute(
                "UPDATE users SET password_hash = ? WHERE email = ?",
                (users.hash_password(PASSWORD), email),
            )
            cls.tokens[role] = cls.sign_in(email)

    @classmethod
    def sign_in(cls, email):
        status, _, data, _ = cls.app.handle(
            "POST", "/api/login",
            {"content-type": "application/json"},
            json.dumps({"email": email, "password": PASSWORD}).encode("utf-8"),
        )
        assert status == 200, email
        return json.loads(data)["token"]

    def call(self, route, token=None):
        headers = {"authorization": "Bearer " + token} if token else {}
        status, _, _, _ = self.app.handle(route.method, fill_path(route.path), headers, b"")
        return status

    def test_protected_routes_refuse_anonymous_requests(self):
        for route in self.app.routes:
            if route.roles is None:
                continue
            with self.subTest(route=f"{route.method} {route.path}"):
                self.assertEqual(self.call(route), 401)

    def test_protected_routes_refuse_roles_that_are_not_allowed(self):
        for route in self.app.routes:
            if route.roles is None:
                continue
            for role, token in self.tokens.items():
                if role in route.roles:
                    continue
                with self.subTest(route=f"{route.method} {route.path}", role=role):
                    status = self.call(route, token)
                    self.assertEqual(status, 403, f"{role} should not reach {route.method} {route.path}")

    def test_public_routes_do_not_require_a_session(self):
        public = [route for route in self.app.routes if route.roles is None]
        self.assertTrue(public, "expected some public routes, such as sign-in")
        for route in public:
            with self.subTest(route=f"{route.method} {route.path}"):
                # Public routes may answer 401 for bad credentials, but must never refuse for role or be missing.
                self.assertNotIn(self.call(route), (403, 404, 405))


if __name__ == "__main__":
    unittest.main()
