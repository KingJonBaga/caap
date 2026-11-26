# Design: NURTURA BHW–RHU Maternal Monitoring and Referral

## 1. Purpose

Evolve the current NURTURA prototype from a general maternal record and appointment tool into a
**BHW-centred monitoring and referral system** for Barangay Health Workers (BHWs), midwives,
physicians and Rural Health Unit (RHU) staff.

The system records, organises and tracks. It does **not** diagnose, treat, or produce unvalidated
risk scores. Every alert requires review by a qualified health professional.

## 2. Current state (what already exists)

| Area | Current implementation |
|---|---|
| Server | Python standard library only: `http.server`, `sqlite3`, `hashlib`, `secrets` (`server/server.py`) |
| API | Route table with role checks in `server/app.py`; JSON over HTTP; bearer-token sessions |
| Database | SQLite at `data/caap.db`; tables: `users`, `sessions`, `password_resets`, `audit_log`, `patients`, `visits`, `caretaker_tasks`, `care_messages`, `caseload_notes` |
| Roles | `patient`, `provider`, `nurse`, `caretaker`, `admin` (`server/users.py`) |
| Front end | Single page (`index.html`) with `showPage()` sections; vanilla JS in `js/`; CSS in `css/` |
| Audit | `audit_log` table written by `server/audit.py` |
| Tests | `tests/test_api.py` (25 tests, `unittest`, in-memory database) |
| Gaps | No BHW or RHU roles; no home visits; no risk indicators; no referral lifecycle; no offline support; no missed-visit list; dashboards use sample data |

## 3. Roles

The PDF separates **RHU Administrator** (operations) from **System Administrator** (technical access).
They must remain separate roles.

| Role | Key | Can do |
|---|---|---|
| Mother | `mother` | View own record, appointments, approved education materials |
| Barangay Health Worker | `bhw` | Register assigned mothers; plan and record home visits; complete approved checklists; raise referrals; see own follow-up tasks |
| Midwife | `midwife` | As BHW, plus record prenatal/postnatal observations; review referrals in their area |
| Physician | `physician` | Review flagged records; document professional findings; close referrals |
| RHU Administrator | `rhu_admin` | Review and validate records; assign BHWs to areas; monitor referral status; reports for the RHU |
| System Administrator | `sysadmin` | Accounts, roles, configuration, backups, audit log. **No** access to clinical content unless also granted a clinical role |

Existing roles map as follows: `patient` → `mother`; `provider` → `physician`; `nurse` → `midwife`;
`caretaker` → kept only for the current demo, or retired after migration; `admin` → split into
`rhu_admin` and `sysadmin`.

**Migration.** The `users.role` column has a `CHECK` constraint. SQLite cannot alter a `CHECK`
in place, so the migration rebuilds the `users` table (create new table, copy rows with the role
mapping above, drop old, rename), inside one transaction, and is covered by a test on a copy of
the current database.

## 4. Data model (additions)

All new tables keep `created_at`, and clinical tables keep `created_by` and `updated_at`.
Changes to clinical records are written to `audit_log`.

- **`mothers`**: `id`, `name`, `date_of_birth` (optional), `address_area`, `barangay_id`,
  `contact` (optional, encrypted at rest or stored outside the main table), `edd` (expected date
  of delivery), `assigned_bhw_id`, `facility_id`, `status` (`prenatal` | `postpartum` | `closed`),
  `duplicate_key` (hashed identifiers used to prevent duplicate registration), `consent_recorded_at`.
- **`pregnancy_history`**: `mother_id`, `gravida`, `para`, `previous_complications` (free text),
  `recorded_by`.
- **`prenatal_visits`** / **`postnatal_visits`**: `mother_id`, `scheduled_for`, `status`
  (`scheduled` | `completed` | `missed` | `rescheduled`), `performed_by`, `vitals` (JSON:
  blood pressure, weight, fetal heart rate where applicable), `observations`.
- **`home_visits`**: `mother_id`, `bhw_id`, `scheduled_for`, `status` (`planned` | `completed` |
  `missed` | `rescheduled`), `checklist_id`, `checklist_answers` (JSON), `notes`, `submitted_to_rhu_at`.
- **`checklists`**: approved assessment forms. `id`, `title`, `stage` (`prenatal` | `postpartum`),
  `version`, `approved_by` (qualified professional), `approved_at`, `items` (JSON). Only checklists
  with `approved_by` set can be used in the field.
- **`risk_indicators`**: `id`, `code`, `description`, `source_reference`, `rule` (JSON),
  `approved_by`, `approved_at`, `active`. **The table starts empty.** Rules are entered only after
  a qualified professional approves them. No thresholds are invented in code.
- **`risk_flags`**: `mother_id`, `indicator_id` or free-text `observation`, `raised_by`,
  `raised_at`, `status` (`open` | `reviewed` | `dismissed`), `reviewed_by`, `review_note`.
- **`referrals`**: `id`, `mother_id`, `from_user_id`, `to_facility_or_user`, `reason`,
  `status` (`pending` | `acknowledged` | `attended` | `closed`), `status_history` (rows in
  `referral_events`), `due_by`. A referral cannot be closed without a recorded outcome.
- **`referral_events`**: `referral_id`, `status`, `actor`, `at`, `note`. Append-only.
- **`reminders`**: `mother_id` or `user_id`, `channel` (`in_app` | `sms`), `message`, `send_at`,
  `sent_at`, `failed_reason`.
- **`education_items`**: approved materials. `title`, `stage`, `body`, `approved_by`, `approved_at`.
- **`sync_outbox`** (server side, for deduplication): `client_id` (UUID created on the device),
  `entity`, `payload_hash`, `received_at`. Used to ignore repeated uploads.

## 5. Offline-first field use

BHWs often work where there is no signal. Requirements:

- The field screens (mother lookup, home-visit checklist, flag, referral) work offline.
- The browser stores pending changes in **IndexedDB** with a local UUID (`client_id`) for each
  change. A **service worker** caches the app shell so it opens offline.
- When connectivity returns, the client uploads the outbox in order. The server uses
  `sync_outbox.client_id` to make uploads idempotent.
- **Conflict rule:** the server keeps the latest change per field, and every rejected or
  overwritten value is written to `audit_log` with both values. Clinical flags are never silently
  overwritten: two flags for the same visit are both kept.
- **Device loss:** offline data is encrypted with a key derived from the user's session at sign-in,
  and the local store is cleared on sign-out. A remote "wipe this device" is a later step; for now
  the session timeout limits exposure.
- Sync status is always visible (pending count, last sync time, failed items with reasons).

## 6. Risk indicators and referrals

1. A professional approves each indicator and its source (`risk_indicators.approved_by`).
2. A BHW or midwife records an observation against a mother. If it matches an active indicator,
   the system creates a `risk_flags` row with `status = open`. Otherwise the BHW can still raise a
   free-text flag. The system never assigns a risk score.
3. Every open flag appears in the RHU queue, ordered by age, with a visible label: *"Requires
   review by a qualified health professional."*
4. A reviewer either dismisses the flag with a note, or creates a referral.
5. A referral moves through `pending → acknowledged → attended → closed`. Each step is an
   `referral_events` row. A referral that is not acknowledged within the configured time shows as
   overdue on the RHU dashboard.
6. A mother with an open referral stays on the follow-up list until the referral is closed. Coverage
   does not end automatically at delivery; it ends when the postpartum period in the approved
   protocol ends, or when a professional closes the case.

## 7. Appointments, home visits and missed-visit monitoring

- Prenatal and postnatal visits and home visits are all "scheduled work". They share one follow-up
  list per BHW and per RHU.
- A visit that is past its scheduled time and not marked completed becomes `missed` automatically
  (a server check at sign-in and on each dashboard load; no background job is needed for the
  prototype).
- The BHW's home screen lists: visits due today, overdue visits, missed visits to reschedule, and
  referrals awaiting an update.
- Reminders are created when a visit is scheduled. Sending is pluggable: `in_app` works now;
  `sms` is an adapter that is added only when the partner confirms an SMS gateway. Failed sends are
  recorded with `failed_reason` and shown to the sender.

## 8. Dashboards and reports

Each dashboard uses **database queries only**. Sample numbers are removed from pages that will
show real data.

- **BHW:** assigned mothers by stage; home visits due, completed, missed, rescheduled.
- **RHU:** counts of registered pregnant and postpartum mothers; mothers under prenatal monitoring;
  postpartum mothers needing follow-up; open flags; referrals by status; overdue referrals.
- **Monthly report:** aggregated counts only (no names, no contact details). Groups smaller than an
  agreed minimum size (for example 5) are shown as "<5" to protect privacy. The export is logged in
  `audit_log`.
- **GIS (optional, later):** barangay-level counts only. Exact locations are never stored in the
  dashboard data.

## 9. Security and privacy

- Keep the role-checked route table in `server/app.py`. Each new route declares its allowed roles.
- Passwords stay scrypt-hashed; session tokens stay hashed at rest.
- Every read of a mother's record and every change is written to `audit_log`.
- Apply the **Philippine Data Privacy Act of 2012 (RA 10173)**. Before live use, document consent,
  privacy notice, retention periods, backup and secure deletion with the implementation partner.
- Use dummy or anonymised data for development and tests. The repository must not contain real
  patient data or real passwords.
- Do not claim integration with national or government health databases.

## 10. Interface changes

- Keep the existing visual style (`css/auth-polish.css`, `css/polish.css`).
- Each role gets its own workspace section in `index.html`, opened by `showPage()`.
- The BHW screen is designed for a phone first: large tap targets, one task per card, works offline.
- Accessibility: keyboard navigation, visible focus, labels on every field, reduced-motion support
  (already in place).

## 11. Testing

- Unit-style tests in `tests/` for each new module (visits, flags, referrals, sync, migration).
- Role tests: every new route has allowed and denied cases (`401` not signed in, `403` wrong role).
- Migration test: run the role migration on a copy of a database with the current seed data.
- Offline test: simulate offline edits, reconnect, and check idempotent upload and conflict logging.
- Privacy test: aggregated reports contain no names and respect the minimum group size.
- Manual check on a phone-sized screen for the BHW flow.

## 12. Open questions for the proponents (must be answered before building the affected parts)

1. Which barangay health centre and RHU will test the system? (Needed for requirements and for
   checklists.)
2. Which maternal assessment forms and risk indicators are approved, and by whom?
3. Will mothers have accounts, or will staff operate the system for them?
4. Which referral and follow-up times are required by the local protocol?
5. Is an SMS gateway available? If not, reminders stay in-app.
6. What are the retention and consent rules agreed with the partner?
