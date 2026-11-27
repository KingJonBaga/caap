"""Task 11.2: the code contains no known passwords and no password values in the seed data.

Scans the project's text files. Local data (data/) and git metadata are skipped."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "data", "__pycache__", "node_modules"}
TEXT_SUFFIXES = {".py", ".js", ".html", ".css", ".json", ".md", ".bat", ".txt", ".kiro"}
# Demo passwords that were removed from the code. None may appear again.
KNOWN_DEMO_PASSWORDS = ("staff2026", "patient2026", "doctor2026", "admin123")
THIS_FILE = Path(__file__).resolve()


def project_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if path.resolve() == THIS_FILE:
            continue
        yield path


class NoSecretsTests(unittest.TestCase):
    def test_no_known_demo_passwords_in_project_files(self):
        found = []
        for path in project_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for password in KNOWN_DEMO_PASSWORDS:
                if password in text:
                    found.append(f"{path.relative_to(ROOT)} contains {password!r}")
        self.assertEqual(found, [])

    def test_seed_users_file_has_no_password_fields(self):
        accounts = json.loads((ROOT / "server" / "seed-users.json").read_text(encoding="utf-8"))
        for account in accounts:
            self.assertNotIn("password", account, f"{account.get('email')} has a password in the seed file")


if __name__ == "__main__":
    unittest.main()
