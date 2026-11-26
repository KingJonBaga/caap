# Manual Test: Offline Editing on a Phone-Sized Screen

**Spec:** NURTURA BHW–RHU Maternal Monitoring and Referral  
**Task:** 8.7 — Test offline editing on a phone-sized screen with network disabled, then reconnect  
**Design reference:** `design.md` §5 (Offline-first field use) and §11 (Testing)  
**Tester role required:** BHW (`b.reyes@bhw.example` / `test-only-staff-pass` in dev)

---

## Prerequisites

| Item | Requirement |
|---|---|
| Browser | Google Chrome (DevTools required) |
| Server | Running locally — `start.bat` or `py -m server.server` from the project root |
| Seed data | At least one registered mother in the database |
| Screen | Physical phone or Chrome DevTools device emulation |

---

## Part 1 — Setup

### 1.1 Start the server

```
start.bat
```

Confirm the console shows the server is listening (default port 8000).

### 1.2 Open the app

Navigate to `http://localhost:8000` in Chrome.

### 1.3 Verify the Service Worker is registered

1. Open **DevTools** (`F12` or `Ctrl+Shift+I`).
2. Go to **Application → Service Workers**.
3. Confirm `sw.js` is listed with status **activated and running**.
4. If it is not registered: hard-refresh (`Ctrl+Shift+R`), then check again.
   If still missing, check the Console for registration errors.

### 1.4 Set viewport to phone size

1. Click the **Toggle Device Toolbar** icon in DevTools (or `Ctrl+Shift+M`).
2. In the device dropdown select **iPhone 14** (390 × 844) or type `390` × `844` manually.
3. Set DPR to `3` and confirm the layout reflows to a single-column view.

### 1.5 Sign in as a BHW

1. Enter the BHW credentials and sign in.
2. Confirm the BHW workspace loads and the **sync bar** is visible showing
   - "All synced" (or a pending count from a prior session)
   - "Online" badge
   - "Sync now" button

---

## Part 2 — Go offline

### 2.1 Disable the network

1. In DevTools, open the **Network** tab.
2. Check the **Offline** checkbox (or select "Offline" from the throttling dropdown).
3. Confirm the **sync bar** badge changes to **"Offline"** immediately.

> The service worker should keep the app shell functional.  If the page shows a network error,
> the service worker cache may be incomplete — note the failure and continue.

---

## Part 3 — Perform offline actions

Each action should be accepted by the UI without a network round-trip and queued in the
IndexedDB outbox.  After each action confirm the sync bar **pending count increments**.

### 3.1 Submit a home-visit checklist

1. Find a mother in the BHW's list.
2. Open **Schedule a home visit** (or complete an existing planned visit).
3. Fill in the checklist fields and tap **Submit**.
4. Expected: a success toast ("Saved offline. Will sync when you reconnect.") appears.
5. Expected: pending count in the sync bar increases by 1.

### 3.2 Raise a risk flag

1. Open the same mother's record.
2. Tap **Raise flag** and enter a free-text observation (e.g. "Elevated BP noted during visit").
3. Submit the flag.
4. Expected: toast confirms the item was queued offline.
5. Expected: pending count increases by 1 (now 2 total).

### 3.3 Create a referral

1. From the flag or the mother's record tap **Create referral**.
2. Enter a reason and a destination facility (e.g. "RHU-001").
3. Submit.
4. Expected: toast confirms offline queue.
5. Expected: pending count increases by 1 (now 3 total).

### 3.4 Confirm the sync bar state

Verify the sync bar shows:
- Pending count: **3** (or the number of actions performed above)
- Online badge: **Offline**
- Last sync time: unchanged from before going offline

---

## Part 4 — Re-enable the network

### 4.1 Bring the network back

1. In DevTools Network tab, uncheck **Offline**.
2. Expected: the **Online** badge reappears in the sync bar within a few seconds.
3. Expected: the app auto-syncs on the `online` event (see `sync-ui.js`) — the pending count
   should drop to 0 without any manual action.

### 4.2 Manual sync (if auto-sync did not fire)

If the pending count has not changed after 5 seconds:

1. Tap the **Sync now** button in the sync bar.
2. Expected: button shows "Syncing…" briefly then returns to "Sync now".
3. Expected: a toast reports how many items were synced (e.g. "Synced 3 item(s).").

---

## Part 5 — Verify server state

### 5.1 Check pending count returns to 0

Confirm the sync bar shows:
- **"All synced"**
- **Last sync time** updated to within the last minute
- Failed items list: **empty**

### 5.2 Confirm records appear on the server

Using a second browser tab or the server's admin view:

1. Sign in as a midwife or RHU admin.
2. Open the mother's record and confirm:
   - The home visit submitted in step 3.1 is present with status `completed` or `planned`.
   - The risk flag raised in step 3.2 is in the open flags queue with the observation text.
   - The referral created in step 3.3 is present with status `pending`.
3. Open the **RHU flags queue** and confirm the flag is labelled
   _"Requires review by a qualified health professional."_

### 5.3 Verify idempotency (optional regression check)

1. Using DevTools Application → Service Workers, click **Update** to force a fresh load.
2. While still signed in, tap **Sync now** again.
3. Expected: response shows 0 synced, 0 failed — no duplicate records appear on the server
   (the `sync_outbox.client_id` deduplication must prevent re-insertion).

---

## Part 6 — Sign out and verify IndexedDB is cleared

### 6.1 Sign out

1. Tap the sign-out button.
2. Expected: the session ends and the login page is shown.

### 6.2 Inspect IndexedDB

1. In DevTools, go to **Application → Storage → IndexedDB → nurtura-offline → outbox**.
2. Click the **Refresh** icon in the DevTools panel.
3. Expected: the `outbox` object store is **empty** (0 entries).

> If entries remain, the `clearStore()` call in `offline-store.js` did not fire on sign-out.
> Record the browser console output and the remaining entry count as a defect.

---

## Pass / Fail Criteria

| # | Check | Pass condition |
|---|---|---|
| P1 | Service worker registered | `sw.js` shows "activated and running" |
| P2 | Offline badge | Sync bar shows "Offline" when network is disabled |
| P3 | Home visit queued | Pending count increments; toast shown |
| P4 | Risk flag queued | Pending count increments; toast shown |
| P5 | Referral queued | Pending count increments; toast shown |
| P6 | Auto or manual sync succeeds | Pending count drops to 0; last sync time updates |
| P7 | Home visit on server | Record visible in server UI / API |
| P8 | Risk flag on server | Flag in open queue with correct label |
| P9 | Referral on server | Referral visible with status `pending` |
| P10 | Idempotency | Second sync creates no duplicate records |
| P11 | Sign-out clears IndexedDB | `outbox` object store is empty after sign-out |

---

## Notes for the tester

- Run this test on both a desktop Chrome in device-emulation mode **and** on a real Android
  phone using Chrome (connect via USB and use `chrome://inspect`).
- If the auto-sync on `online` does not fire, check that the `online` event listener in
  `js/sync-ui.js` is active (no JS errors in console).
- Failed sync items appear in the expandable **failed items** section of the sync bar with
  their error reasons.  Screenshot these before retrying.
- Record the Chrome and Android versions used so results are reproducible.
