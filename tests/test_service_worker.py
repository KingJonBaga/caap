"""tests/test_service_worker.py

Automated checks for the NURTURA app-shell service worker (Phase 8, task 8.1).

What this file covers
─────────────────────
1. Static file existence checks  — sw.js exists, is non-empty, and declares
   the expected CACHE_NAME and the core app-shell files.
2. Registration check             — js/init.js contains the serviceWorker.register call.
3. API exclusion check            — sw.js does NOT cache /api/* paths.

Manual verification (browser required)
───────────────────────────────────────
The browser-specific behaviour cannot be tested without a live origin, so verify
it manually after starting the server:

  1. Run:  py server/server.py   (or double-click start.bat)
  2. Open  http://localhost:3000/  in Chrome or Edge.
  3. DevTools → Application → Service Workers
       ✓  Status should show "activated and is running"
  4. DevTools → Application → Cache Storage → "nurtura-app-shell-v1"
       ✓  All CSS and JS files from css/ and js/ should be listed.
  5. DevTools → Network → tick "Offline"
  6. Reload the page (Ctrl+R).
       ✓  The page should load from cache without any network errors.
  7. Open DevTools → Network, reload again (still offline).
       ✓  Requests for index.html and static assets show "(Service Worker)" as the initiator.
  8. Untick "Offline", make a POST to /api/login.
       ✓  The request is NOT served from cache (it goes to the network).
"""

import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # project root (caap/)


class TestServiceWorkerFile(unittest.TestCase):
    """sw.js exists and contains the required constants."""

    def setUp(self):
        self.sw_path = ROOT / "sw.js"
        self.sw_text = self.sw_path.read_text(encoding="utf-8")

    def test_sw_js_exists(self):
        """sw.js must be present in the project root."""
        self.assertTrue(self.sw_path.is_file(), "sw.js not found in the project root")

    def test_sw_js_is_non_empty(self):
        self.assertGreater(len(self.sw_text.strip()), 0, "sw.js is empty")

    def test_cache_name_declared(self):
        """CACHE_NAME constant must be present and start with 'nurtura-app-shell'."""
        match = re.search(r'const\s+CACHE_NAME\s*=\s*[\'"]([^\'"]+)[\'"]', self.sw_text)
        self.assertIsNotNone(match, "CACHE_NAME constant not found in sw.js")
        self.assertTrue(
            match.group(1).startswith("nurtura-app-shell"),
            f"CACHE_NAME should start with 'nurtura-app-shell', got: {match.group(1)!r}",
        )

    def test_install_event_present(self):
        self.assertIn("addEventListener('install'", self.sw_text, "install event handler missing from sw.js")

    def test_activate_event_present(self):
        self.assertIn("addEventListener('activate'", self.sw_text, "activate event handler missing from sw.js")

    def test_fetch_event_present(self):
        self.assertIn("addEventListener('fetch'", self.sw_text, "fetch event handler missing from sw.js")

    def test_index_html_in_cache_list(self):
        self.assertIn("'/index.html'", self.sw_text, "'/index.html' not in urlsToCache")

    def test_api_calls_not_cached(self):
        """sw.js must exclude /api/* paths from cache handling."""
        self.assertIn("/api/", self.sw_text, "sw.js does not reference /api/ at all")
        # Confirm there is a guard that skips caching for /api/ paths
        self.assertTrue(
            "startsWith('/api/')" in self.sw_text or "pathname.startsWith('/api/')" in self.sw_text,
            "sw.js does not contain an /api/ exclusion guard",
        )

    def test_css_files_in_cache_list(self):
        for css_file in [
            "tokens.css", "typography.css", "login.css", "inputs.css",
            "buttons.css", "dashboard.css", "auth-polish.css", "polish.css",
        ]:
            with self.subTest(css=css_file):
                self.assertIn(
                    css_file, self.sw_text,
                    f"CSS file '{css_file}' is missing from urlsToCache in sw.js",
                )

    def test_js_files_in_cache_list(self):
        for js_file in [
            "auth.js", "init.js", "navigation.js", "session.js",
            "utils.js", "admin.js", "provider.js", "caretaker.js",
        ]:
            with self.subTest(js=js_file):
                self.assertIn(
                    js_file, self.sw_text,
                    f"JS file '{js_file}' is missing from urlsToCache in sw.js",
                )

    def test_skip_waiting_called(self):
        """skipWaiting() ensures the new SW activates without a second reload."""
        self.assertIn("skipWaiting()", self.sw_text, "self.skipWaiting() not found in sw.js")

    def test_clients_claim_called(self):
        """clients.claim() lets the new SW control already-open pages."""
        self.assertIn("clients.claim()", self.sw_text, "self.clients.claim() not found in sw.js")


class TestServiceWorkerRegistration(unittest.TestCase):
    """js/init.js contains the service-worker registration block."""

    def setUp(self):
        self.init_path = ROOT / "js" / "init.js"
        self.init_text = self.init_path.read_text(encoding="utf-8")

    def test_init_js_exists(self):
        self.assertTrue(self.init_path.is_file(), "js/init.js not found")

    def test_sw_registration_present(self):
        self.assertIn(
            "serviceWorker",
            self.init_text,
            "serviceWorker not referenced in js/init.js",
        )

    def test_sw_register_call_present(self):
        self.assertIn(
            "register('/sw.js')",
            self.init_text,
            "navigator.serviceWorker.register('/sw.js') not found in js/init.js",
        )

    def test_sw_support_guard_present(self):
        """Registration must be guarded with 'serviceWorker' in navigator."""
        self.assertIn(
            "'serviceWorker' in navigator",
            self.init_text,
            "Guard ''serviceWorker' in navigator' missing from js/init.js",
        )


if __name__ == "__main__":
    unittest.main()
