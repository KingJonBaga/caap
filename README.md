# NURTURA: maternal health monitoring and referral

NURTURA helps Barangay Health Workers (BHWs), midwives, physicians and Rural Health Unit (RHU) staff
record and follow up on pregnant and postpartum mothers, document home visits, flag concerns for
professional review, and track referrals.

NURTURA records, organises and tracks. It does **not** diagnose, treat, or produce risk scores.
Every alert needs review by a qualified health professional.

> **Status:** this is a development version. Do not enter real patient information until a partner
> health centre has approved the system, the forms and the privacy rules. See
> `.kiro/specs/nurtura-bhw-referral/tasks.md` for what is still open.

## Running it (Windows)

1. Install Python 3.12 or newer from python.org. Tick **Add python.exe to PATH**.
2. Double-click `start.bat`. Keep its window open while you use the app.
3. Open http://localhost:3000 in your browser.

The first time the database is created, each starting account gets a random password. Those
passwords are written to `data/first-run-logins.txt`. Keep that file private and delete it once you
have saved the details. The `data/` folder is never committed to git.

If `py` is not recognised, use `python` in its place.

## Roles

| Role | Who | Main tasks |
|---|---|---|
| `mother` | Pregnant or postpartum mother | See approved information for her stage |
| `bhw` | Barangay Health Worker | Register assigned mothers, plan and record home visits, raise flags and referrals |
| `midwife` | Midwife | Record prenatal and postnatal observations, review referrals |
| `physician` | Doctor | Review flagged records, add approved education material, close referrals |
| `rhu_admin` | RHU administrator | Review flags and referrals, monitor coverage, export the monthly report |
| `sysadmin` | System administrator | Accounts, roles and configuration. No clinical access by default |

Every protected route checks the signed-in role. Requests without a session get `401`, and requests
from a role that isn't allowed get `403`.

## Tests

```
py -m unittest discover -s tests
```

The tests use temporary in-memory databases, so they never change `data/caap.db`.

## Project layout

- `server/`: Python server (standard library only): routes, database and each feature.
- `js/`, `css/`, `index.html`, `sw.js`: the web pages, offline support and styling.
- `tests/`: automated tests.
- `docs/`: test guides, such as the offline phone test.
- `.kiro/specs/nurtura-bhw-referral/`: the design, task list and build prompt.

## Privacy and security

- Passwords are stored as salted scrypt hashes. Session tokens are stored as SHA-256 hashes.
- Each read of a mother's record and each change is written to the audit log.
- Use dummy data for development and testing. The repository must not contain real patient data or
  real passwords. A test checks for known demo passwords.
- Before live use, the partner must confirm the consent text, the privacy notice and the retention
  rules, in line with the Philippine Data Privacy Act of 2012 (RA 10173).
