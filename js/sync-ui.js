/**
 * sync-ui.js — BHW sync status display (design.md §5, task 8.5)
 *
 * Shows, always visible on the BHW screen:
 *   - pending outbox count  ("3 pending" / "All synced")
 *   - last sync time        ("Last synced: 2 min ago" / "Never synced")
 *   - failed items with reasons (expandable list)
 *   - a "Sync now" button
 *
 * Auto-syncs when the browser reports connectivity returning (online event),
 * and refreshes the display after every sync (nurtura:sync-complete event).
 *
 * All DOM hooks live inside #bhwSyncBar in the BHW workspace page.
 */

(function () {
  'use strict';

  const LAST_SYNC_KEY = 'nurtura_last_sync';

  // ── Helpers ───────────────────────────────────────────────────────────────

  /** "2 min ago", "just now", etc. for an ISO timestamp. */
  function timeAgo(iso) {
    if (!iso) return 'Never synced';
    const then = new Date(iso).getTime();
    if (Number.isNaN(then)) return 'Never synced';
    const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
    if (secs < 10)    return 'Last synced: just now';
    if (secs < 60)    return 'Last synced: ' + secs + 's ago';
    const mins = Math.floor(secs / 60);
    if (mins < 60)   return 'Last synced: ' + mins + ' min ago';
    const hrs  = Math.floor(mins / 60);
    if (hrs  < 24)   return 'Last synced: ' + hrs + 'h ago';
    const days = Math.floor(hrs / 24);
    return 'Last synced: ' + days + 'd ago';
  }

  function esc(str) {
    return String(str == null ? '' : str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function isBhwPage() {
    const p = document.getElementById('page-bhw');
    return !!(p && p.classList.contains('active'));
  }

  // ── Display update ─────────────────────────────────────────────────────────

  /**
   * Refresh the sync bar from the outbox.  Safe to call any time; no-ops when
   * the BHW page or the store is unavailable.
   */
  async function updateSyncDisplay() {
    const bar = document.getElementById('bhwSyncBar');
    if (!bar || !window.nurturaOfflineStore) return;

    let status;
    try {
      status = await window.nurturaOfflineStore.getSyncStatus();
    } catch (_) {
      status = { pendingCount: 0, failedCount: 0, failedItems: [], lastSyncTime: null };
    }

    const pendingEl  = document.getElementById('bhwSyncPending');
    const timeEl     = document.getElementById('bhwSyncTime');
    const failedEl   = document.getElementById('bhwSyncFailed');
    const detailEl   = document.getElementById('bhwSyncDetails');
    const onlineEl   = document.getElementById('bhwSyncOnline');

    if (pendingEl) {
      pendingEl.textContent = status.pendingCount > 0
        ? status.pendingCount + ' pending'
        : 'All synced';
      pendingEl.classList.toggle('has-pending', status.pendingCount > 0);
    }
    if (timeEl) {
      timeEl.textContent = timeAgo(status.lastSyncTime);
    }
    if (onlineEl) {
      const online = navigator.onLine;
      onlineEl.textContent = online ? 'Online' : 'Offline';
      onlineEl.classList.toggle('is-offline', !online);
    }
    if (failedEl) {
      failedEl.textContent = status.failedCount > 0
        ? status.failedCount + ' failed'
        : '';
      failedEl.hidden = status.failedCount === 0;
    }
    if (detailEl) {
      if (status.failedItems.length === 0) {
        detailEl.innerHTML = '';
        detailEl.hidden = true;
      } else {
        detailEl.innerHTML = status.failedItems.map((item) =>
          '<div class="bhw-sync-failed-item">' +
            '<strong>' + esc(item.entity) + '</strong>' +
            '<span>' + esc(item.failed_reason || 'Unknown error') + '</span>' +
            '<small>' + esc(item.created_at || '') + '</small>' +
          '</div>'
        ).join('');
        // Keep the expanded state the user chose.
        const toggle = document.getElementById('bhwSyncFailedToggle');
        detailEl.hidden = !(toggle && toggle.getAttribute('aria-expanded') === 'true');
      }
    }
  }

  // Exposed for the button onclick and for other modules.
  window.updateSyncDisplay = updateSyncDisplay;

  // ── Actions ───────────────────────────────────────────────────────────────

  /** Run a sync now, then refresh the display.  Wired to the "Sync now" button. */
  async function syncNowFromUi() {
    const btn = document.getElementById('bhwSyncNow');
    if (!window.nurturaOfflineStore) return;
    if (btn) { btn.disabled = true; btn.textContent = 'Syncing…'; }
    try {
      const result = await window.nurturaOfflineStore.syncNow();
      if (result && result.errors && result.errors.length > 0) {
        if (typeof showToast === 'function') {
          showToast('Sync finished with ' + result.failed + ' failed item(s).', 'bhwToast');
        }
      } else if (result && result.synced > 0) {
        if (typeof showToast === 'function') {
          showToast('Synced ' + result.synced + ' item(s).', 'bhwToast');
        }
      }
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = 'Sync now'; }
      await updateSyncDisplay();
    }
  }
  window.bhwSyncNow = syncNowFromUi;

  /** Toggle the failed-items details list. */
  function toggleFailedDetails() {
    const detailEl = document.getElementById('bhwSyncDetails');
    const toggle  = document.getElementById('bhwSyncFailedToggle');
    if (!detailEl || !toggle) return;
    const expand = detailEl.hidden;
    detailEl.hidden = !expand;
    toggle.setAttribute('aria-expanded', String(expand));
  }
  window.bhwToggleFailedDetails = toggleFailedDetails;

  // ── Initialisation ────────────────────────────────────────────────────────

  function initSyncUi() {
    // Wire events once.  The sync bar lives in the BHW page; the online /
    // sync-complete listeners are cheap no-ops when it is not present.
    window.addEventListener('online', async () => {
      await updateSyncDisplay();
      if (isBhwPage() && window.nurturaOfflineStore) {
        // Auto-sync when connectivity returns (design.md §5).
        await window.nurturaOfflineStore.syncNow().catch(() => {});
      }
    });
    window.addEventListener('offline', () => updateSyncDisplay());
    window.addEventListener('nurtura:sync-complete', () => updateSyncDisplay());

    // Refresh when the BHW page becomes active (hooked into showPage).
    document.addEventListener('pagechange: bhw', () => updateSyncDisplay());

    // Initial paint.
    updateSyncDisplay();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSyncUi);
  } else {
    initSyncUi();
  }
})();

// Re-dispatch showPage('bhw') as a DOM event so sync-ui can react without
// modifying navigation.js.  Wrapped so a missing showPage never breaks.
(function () {
  const original = window.showPage;
  if (typeof original !== 'function') return;
  window.showPage = function (name) {
    const result = original.apply(this, arguments);
    try { document.dispatchEvent(new CustomEvent('pagechange: ' + name)); } catch (_) {}
    return result;
  };
})();
