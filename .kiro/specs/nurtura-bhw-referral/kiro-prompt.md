Build the next version of NURTURA, a maternal health management prototype, as a BHW–RHU monitoring and referral system.

Context
- The project is in this folder. The backend is Python standard library only (server/*.py, SQLite at data/caap.db). The front end is one page (index.html) with vanilla JS (js/) and CSS (css/). Tests are in tests/ and run with: py -m unittest discover -s tests
- The current app is a general maternal record and appointment tool. It needs to focus on the workflow in the proponents' guide: Barangay Health Workers (BHWs) and Rural Health Unit (RHU) staff monitoring pregnant and postpartum mothers, doing home visits, flagging concerns, and tracking referrals.

Read these first, in this order:
1. .kiro/specs/nurtura-bhw-referral/design.md
2. .kiro/specs/nurtura-bhw-referral/tasks.md

Requirements
- Roles: mother, BHW, midwife, physician, RHU administrator, system administrator. Keep RHU administrator and system administrator separate. Migrate the existing roles as described in the design.
- Register and track mothers, pregnancy history and consent. Prevent duplicate registrations.
- Record prenatal, postnatal and home visits. Mark visits as missed when they pass their time without completion.
- Use only approved checklists. Never assign a risk score or threshold in code.
- Risk indicators start empty and are entered only with an approving professional and a source reference. Observations create flags for professional review. Free-text flags are always allowed.
- Referrals move through pending, acknowledged, attended, closed in that order, with an append-only history. A referral needs an outcome before it can close.
- Keep mothers with open referrals on the follow-up list after delivery.
- BHW screens work offline, with a local outbox, idempotent sync, and visible sync status.
- Dashboards show real data from the database only. Reports are aggregated, with a minimum group size.
- Log every read of a mother's record and every change to audit_log.
- Follow the Philippine Data Privacy Act of 2012. Do not store real patient data or real passwords in the repository. Use dummy data for tests.
- Keep the existing visual style and reduced-motion support.

How to work
- Follow tasks.md phase by phase. Finish each phase with its tests passing before starting the next.
- Do not build anything that depends on an open question in design.md section 12 until the answer is recorded in tasks.md.
- Write a test for every new route covering: not signed in (401), wrong role (403), and the allowed case.
- Before finishing, search the repository for password literals and real-looking personal data, and report what you found.
- Report what you built, what tests you ran and their results, and what is still open.
