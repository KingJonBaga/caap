"""
test_clear_store.py — Task 8.6: Clear the local store on sign-out.

Browser-side behaviour (IndexedDB / in-memory encryption key)
==============================================================
The offline store is implemented entirely in browser JavaScript
(js/offline-store.js, js/auth.js).  It depends on:

  - IndexedDB  — not available in Python test environments.
  - Web Crypto (SubtleCrypto / AES-GCM) — not available in Python.
  - window / navigator — browser globals only.

The browser-side clearStore() contract (verified by code review):

  1. nurturaOfflineStore.clearStore() sets _encKey = null, so any
     subsequent decrypt attempt throws immediately without touching
     the database.

  2. It then calls store.clear() inside a readwrite IndexedDB transaction
     so every outbox entry is deleted.

  3. auth.js signOut() calls clearStore() inside a try/catch before
     deleting the server session, ensuring the local store is wiped even
     if the server is unreachable.

These guarantees are verified by manual browser testing (design.md §8.7)
and by reading js/offline-store.js and js/auth.js directly.

Server-side behaviour (session invalidation)
============================================
The server half of sign-out is fully testable from Python.  The tests
below confirm that DELETE /api/session removes the token from the
sessions table, so any authenticated API call with the old token
returns 401 immediately after sign-out.

Run with:  py -m unittest discover -s tests
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAFF_PASSWORD = "test-only-staff-pass"
sys.path.insert(0, str(ROOT / "server"))

from app import create_app   # noqa: E402
from db  import open_database  # noqa: E402


class Result:
    def __init__(self, status, content_type, body, headers):
        self.status       = status
        self.content_type = content_type
        self.body         = body
        self.headers      = headers
        self.data = (
            json.loads(body)
            if content_type and content_type.startswith("application/json")
            else body
        )


class Client:
    def __init__(self, app):
        self.app = app

    def request(self, method, path, body=None, token=None):
        headers = {}
        raw     = b""
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            headers["content-type"] = "application/json"
        if token:
            headers["authorization"] = "Bearer " + token
        return Result(*self.app.handle(method, path, headers, raw))

    def sign_in(self, email, password=STAFF_PASSWORD):
        res = self.request(
            "POST", "/api/login", body={"email": email, "password": password}
        )
        assert res.status == 200, f"login failed for {email}: {res.body}"
        return res.data["token"]


class ClearStoreSignOutTests(unittest.TestCase):
    """
    Server-side sign-out tests (task 8.6).

    The browser-side IndexedDB clear (nurturaOfflineStore.clearStore) cannot be
    driven from Python; those assertions are documented above and covered by
    manual browser testing (design.md §8.7).
    """

    @classmethod
    def setUpClass(cls):
        cls.db     = open_database(":memory:", shared_password=STAFF_PASSWORD)
        cls.app    = create_app(cls.db, log=lambda _: None)
        cls.client = Client(cls.app)

    def req(self, method, path, **kwargs):
        return self.client.request(method, path, **kwargs)

    # ── Token invalidation ────────────────────────────────────────────────────

    def test_sign_out_invalidates_token_on_authenticated_endpoint(self):
        """After DELETE /api/session the same token must return 401."""
        token = self.client.sign_in("b.reyes@bhw.example")

        # Token is valid before sign-out — use an endpoint that requires a role.
        before = self.req("GET", "/api/bhw/schedule", token=token)
        self.assertEqual(before.status, 200, "token should be valid before sign-out")

        # Sign out.
        sign_out = self.req("DELETE", "/api/session", token=token)
        self.assertEqual(sign_out.status, 200, "DELETE /api/session should return 200")

        # Same token is now invalid on a role-guarded endpoint.
        after = self.req("GET", "/api/bhw/schedule", token=token)
        self.assertEqual(
            after.status, 401,
            "old token must return 401 after sign-out (session row deleted from DB)"
        )

    def test_sign_out_without_a_session_still_returns_200(self):
        """
        Signing out when already signed out (or with no token) must not raise.
        The front-end auth.js calls DELETE /api/session unconditionally; it
        must succeed even if the session was already cleaned up.
        """
        res = self.req("DELETE", "/api/session")  # no token header
        self.assertEqual(
            res.status, 200,
            "sign-out with no token should still succeed (no-op server side)"
        )

    def test_sign_out_does_not_affect_other_active_sessions(self):
        """
        Signing out one session must not invalidate a concurrent session for
        a different user.
        """
        token_bhw      = self.client.sign_in("b.reyes@bhw.example")
        token_midwife  = self.client.sign_in("c.lim@rhu.example")

        # Sign the BHW out.
        self.req("DELETE", "/api/session", token=token_bhw)

        # Midwife's session must still be valid (use a role-guarded endpoint).
        still_valid = self.req("GET", "/api/visits", token=token_midwife)
        self.assertEqual(
            still_valid.status, 200,
            "signing out one user must not end another user's session"
        )

    def test_sign_out_then_sign_in_again_issues_new_token(self):
        """
        After sign-out, the user must be able to sign in again and receive a
        fresh token that is different from the invalidated one.
        """
        email = "b.reyes@bhw.example"
        old_token = self.client.sign_in(email)

        self.req("DELETE", "/api/session", token=old_token)

        new_token = self.client.sign_in(email)
        self.assertNotEqual(
            old_token, new_token,
            "a new sign-in after sign-out must produce a different token"
        )

        # New token is usable on a role-guarded endpoint (bhw schedule).
        res = self.req("GET", "/api/bhw/schedule", token=new_token)
        self.assertEqual(res.status, 200)


if __name__ == "__main__":
    unittest.main()
