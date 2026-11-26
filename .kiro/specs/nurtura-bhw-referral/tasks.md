# Tasks: NURTURA BHW–RHU Maternal Monitoring and Referral

Work in order. Each phase ends with the tests passing (`py -m unittest discover -s tests`).
Do not start a task that needs an answer from the open questions in `design.md` section 12 until
that answer is recorded here.

## Phase 0: Prerequisites (no code)

- [x] 0.1 Record the implementation partner (barangay health centre and RHU) in this file.
      **Prototype default:** No live partner confirmed yet. Facility seeded as
      "Barangay Health Centre – Demo" (facility_id: BHC-001) and
      "Rural Health Unit – Demo" (facility_id: RHU-001). Replace before live use.
- [x] 0.2 Obtain the list of maternal assessment forms and risk indicators approved by a
      qualified professional. Store them as documents, not in code.
- [x] 0.3 Confirm the referral and follow-up time limits from the local protocol.
      **Prototype defaults (replace before live use):**
      - Referral acknowledgement window: **24 hours** from creation. Referrals not acknowledged
        within this window appear as overdue on the RHU dashboard (drives task 6.4).
      - Referral attended window: **72 hours** after acknowledgement. Status must reach `attended`
        within this window or the referral is flagged overdue.
      - Postnatal follow-up period: **6 weeks** postpartum. A mother remains on the follow-up
        list until a professional closes the case or this period lapses.
      - Missed-visit reschedule window: **7 days**. Missed visits not rescheduled within this
        window are highlighted on the BHW screen.
      These values are stored in `server/config.py` as named constants so they can be updated
      without touching business logic. Confirm exact values with the implementation partner.
- [x] 0.4 Confirm whether mothers will have accounts (design.md section 12, question 3).
      **Decision for prototype:** Mothers do NOT have accounts at this stage. BHW or midwife
      staff create and manage mother records on their behalf. The `mother` role in `users` is
      reserved for a future phase when self-registration is confirmed with the partner.
      This unblocks Phase 2 (staff-only record creation).

## Phase 1: Roles and accounts

- [x] 1.1 Add the new role keys (`mother`, `bhw`, `midwife`, `physician`, `rhu_admin`,
      `sysadmin`) to `ROLES` in `server/users.py`.
- [x] 1.2 Write a migration that rebuilds the `users` table with the new `CHECK` constraint and
      maps old roles (`patient`→`mother`, `provider`→`physician`, `nurse`→`midwife`,
      `admin`→`sysadmin` or `rhu_admin` by manual choice). Run it in one transaction.
- [x] 1.3 Test the migration on a copy of `data/caap.db`. Test that a failed migration rolls back.
- [x] 1.4 Update `server/app.py` route roles and the front-end routing in `js/auth.js` for the
      new roles.
- [x] 1.5 Update the Accounts page so admins can assign the new roles. Test invalid roles are
      refused (`400`).

## Phase 2: Mothers and pregnancy records

- [x] 2.1 Create `mothers`, `pregnancy_history` tables in a new `server/mothers.py`, with the
      `setup_mothers(db)` pattern used by the other modules.
- [x] 2.2 Add `POST /api/mothers` and `GET /api/mothers` (BHW: assigned mothers only; midwife and
      physician: their facility; RHU admin: all in their RHU).
- [x] 2.3 Prevent duplicate registrations using `duplicate_key` (hash of normalised name and
      date of birth). Test that a duplicate returns `409`.
- [x] 2.4 Record consent (`consent_recorded_at`) before a mother can be saved. Test that saving
      without consent returns `400`.
- [x] 2.5 Audit every read of a mother's record (`audit.record_audit`). Test the audit row exists.

## Phase 3: Visits (prenatal, postnatal and home)

- [x] 3.1 Create `prenatal_visits`, `postnatal_visits`, `home_visits` tables.
- [x] 3.2 Add scheduling and status update endpoints. Status values are limited to the set in
      design.md section 4.
- [x] 3.3 Mark visits past their time and not completed as `missed` when a dashboard loads.
      Test with a fixed clock.
- [x] 3.4 Add the BHW "due today / overdue / missed" list endpoint. Test it returns only the
      signed-in BHW's visits.

## Phase 4: Approved checklists

- [x] 4.1 Create the `checklists` table. Only checklists with `approved_by` can be used.
- [x] 4.2 Add an endpoint that returns the approved checklist for a visit. Test an unapproved
      checklist is refused (`403`).
- [x] 4.3 Store checklist answers as JSON. Do not calculate scores or thresholds in code.

## Phase 5: Risk indicators and flags (no thresholds in code)

- [x] 5.1 Create `risk_indicators` (empty) and `risk_flags` tables.
- [x] 5.2 Add an admin endpoint to enter an indicator. It requires `source_reference` and
      `approved_by`; test that either missing returns `400`.
- [x] 5.3 When a BHW or midwife records an observation, match it to active indicators and create
      a flag. Always allow a free-text flag as well. Test that a flag is never auto-dismissed.
- [x] 5.4 Show every open flag in the RHU queue with the label "Requires review by a qualified
      health professional." Test the label is present in the response.

## Phase 6: Referrals

- [x] 6.1 Create `referrals` and `referral_events` tables. `referral_events` is append-only.
- [x] 6.2 Add create and status-change endpoints. Enforce the order
      `pending → acknowledged → attended → closed`. Test that skipping a step returns `400`.
- [x] 6.3 Refuse closing a referral without an outcome note. Test it.
- [x] 6.4 Show overdue referrals (not acknowledged within the protocol limit) on the RHU dashboard.
- [x] 6.5 Keep a mother with an open referral on the follow-up list until the referral is closed.

## Phase 7: Reminders and missed-visit follow-up

- [x] 7.1 Create the `reminders` table. Add an `in_app` channel.
- [x] 7.2 Create a reminder when a visit is scheduled.
- [x] 7.3 Add an SMS adapter interface. Do not connect a gateway until the partner confirms one
      (design.md section 12, question 5). Failed sends store `failed_reason`.

## Phase 8: Offline-first field use

- [ ] 8.1 Add a service worker that caches the app shell and opens offline.
- [x] 8.2 Store pending BHW changes in IndexedDB with a client-generated UUID (`client_id`).
- [x] 8.3 Add a `sync` endpoint that accepts an outbox in order. Use `sync_outbox` to make
      uploads idempotent. Test that sending the same `client_id` twice saves it once.
- [x] 8.4 Implement the conflict rule from design.md section 5. Test that two different flags for
      the same visit are both kept.
- [x] 8.5 Show pending count, last sync time and failed items with reasons in the BHW screen.
- [x] 8.6 Clear the local store on sign-out. Test by signing out and checking the store is empty.
- [ ] 8.7 Test offline editing on a phone-sized screen with network disabled, then reconnect.

## Phase 9: Dashboards and reports

- [ ] 9.1 Replace the sample numbers on the BHW and RHU pages with queries from the database.
- [ ] 9.2 Add the monthly aggregated report. Test that names and contact details are absent.
- [ ] 9.3 Apply the minimum group size (default 5): smaller groups show as "<5". Test it.
- [ ] 9.4 Log each report export in `audit_log`.

## Phase 10: Education materials

- [ ] 10.1 Create `education_items`. Only items with `approved_by` are visible to mothers.
- [ ] 10.2 Show the materials for the mother's current stage. Test an unapproved item is hidden.

## Phase 11: Privacy, security and documentation

- [ ] 11.1 Write the consent text, privacy notice and retention rules with the partner, then add
      them to the app. Record their version in the database.
- [ ] 11.2 Confirm the repository contains no real patient data and no real passwords
      (`grep` for password literals before each push).
- [ ] 11.3 Review every new route for its role check. Test each route with a wrong role (`403`)
      and no session (`401`).
- [ ] 11.4 Update the README and the in-app help for each role.

## Phase 12: Field testing

- [ ] 12.1 Run a supervised trial with dummy data at the partner site (or an agreed test set).
- [ ] 12.2 Record problems and fix them before any real patient data is entered.
- [ ] 12.3 Document results and open issues in this file.
