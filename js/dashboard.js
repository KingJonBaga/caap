/**
 * dashboard.js
 * Fetches live counts from the server and populates the BHW and RHU dashboard
 * stat cards (task 9.1). Replaces the hardcoded sample numbers in index.html.
 *
 * BHW workspace — /api/bhw/dashboard
 * RHU workspace — /api/rhu/dashboard  (shown when rhu_admin signs in through
 *                 the admin workspace)
 */

'use strict';

// ── Shared helper ─────────────────────────────────────────────────────────────

function _setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function _apiGet(path) {
  const token = window.caapSession && window.caapSession.getToken
    ? window.caapSession.getToken()
    : '';
  return fetch(path, {
    headers: token ? { Authorization: 'Bearer ' + token } : {},
  }).then(function (res) {
    if (!res.ok) {
      return res.json().then(function (d) {
        throw new Error((d && d.error) || ('HTTP ' + res.status));
      }).catch(function () {
        throw new Error('HTTP ' + res.status);
      });
    }
    return res.json();
  });
}

// ── BHW dashboard ─────────────────────────────────────────────────────────────

/**
 * Fetch /api/bhw/dashboard and populate the BHW stat cards.
 * Safe to call repeatedly; silently no-ops when the BHW page is not present.
 */
async function loadBhwDashboard() {
  try {
    const data = await _apiGet('/api/bhw/dashboard');

    // Assigned mothers
    const total = (data.mothers && data.mothers.total) != null
      ? data.mothers.total
      : '—';
    _setText('bhwMotherCount', total);

    // Visits due today (home + all types)
    const dueToday = (data.homeVisits && data.homeVisits.dueToday) != null
      ? data.homeVisits.dueToday
      : '—';
    _setText('bhwVisitsDue', dueToday);
    _setText('bhwVisitsDueMeta', 'Home visits');

    // Overdue (missed/past-due) visits
    const overdue = (data.homeVisits && data.homeVisits.overdue) != null
      ? data.homeVisits.overdue
      : '—';
    _setText('bhwVisitsMissed', overdue);

    // Open referrals count
    const openRefs = data.pendingReferrals != null ? data.pendingReferrals : '—';
    _setText('bhwReferralCount', openRefs);

  } catch (err) {
    console.warn('BHW dashboard load failed:', err);
    // Leave the "—" placeholders; don't crash the page.
  }
}

window.loadBhwDashboard = loadBhwDashboard;

/**
 * Render the BHW visit list from /api/bhw/schedule (due today + overdue).
 */
async function loadBhwVisitList() {
  const listEl = document.getElementById('bhwVisitsList');
  if (!listEl) return;

  try {
    const data = await _apiGet('/api/bhw/schedule');
    const visits = [
      ...(data.dueToday || []).map(function (v) { v._category = 'dueToday'; return v; }),
      ...(data.overdue  || []).map(function (v) { v._category = 'overdue';  return v; }),
      ...(data.missed   || []).map(function (v) { v._category = 'missed';   return v; }),
    ];

    if (visits.length === 0) {
      listEl.innerHTML = '<p class="caretaker-empty">No visits due today or overdue.</p>';
      return;
    }

    listEl.innerHTML = visits.map(function (v) {
      var label = v._category === 'dueToday' ? 'Due today'
                : v._category === 'overdue'  ? 'Overdue'
                :                              'Missed';
      var badgeClass = v._category === 'dueToday' ? 'pov-appt-badge--scheduled'
                     : v._category === 'overdue'  ? 'pov-appt-badge--inroom'
                     :                              'pov-appt-badge--waiting';
      var typeLabel = v.visitType === 'prenatal'  ? 'Prenatal visit'
                    : v.visitType === 'postnatal' ? 'Postnatal visit'
                    :                               'Home visit';
      var date = (v.scheduledFor || v.scheduled_for || '').substring(0, 10);
      return '<div class="pov-clinic-row">'
        + '<span class="pov-clinic-time">' + _esc(date) + '</span>'
        + '<div class="pov-clinic-info">'
        +   '<strong>' + _esc(typeLabel) + '</strong>'
        +   '<span>Mother ID: ' + _esc(v.motherId || v.mother_id || '—') + '</span>'
        + '</div>'
        + '<span class="pov-appt-badge ' + badgeClass + '">' + label + '</span>'
        + '</div>';
    }).join('');
  } catch (err) {
    console.warn('BHW visit list load failed:', err);
  }
}

/**
 * Render the BHW open referrals list from /api/referrals?status=pending.
 */
async function loadBhwReferralsList() {
  const listEl = document.getElementById('bhwReferralsList');
  if (!listEl) return;

  try {
    const data = await _apiGet('/api/referrals?status=pending');
    const refs = data.referrals || [];

    if (refs.length === 0) {
      listEl.innerHTML = '<p class="caretaker-empty">No open referrals.</p>';
      return;
    }

    listEl.innerHTML = refs.slice(0, 10).map(function (r) {
      var date = (r.created_at || '').substring(0, 10);
      return '<div class="pov-clinic-row">'
        + '<span class="pov-clinic-time">' + _esc(date) + '</span>'
        + '<div class="pov-clinic-info">'
        +   '<strong>Mother ID: ' + _esc(r.mother_id) + '</strong>'
        +   '<span>' + _esc(r.reason || 'No reason given') + '</span>'
        + '</div>'
        + '<span class="pov-appt-badge pov-appt-badge--waiting">Pending</span>'
        + '</div>';
    }).join('');
  } catch (err) {
    console.warn('BHW referrals list load failed:', err);
  }
}

function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/** Full BHW workspace refresh: stats + visit list + referrals list. */
async function loadBhwWorkspace() {
  await Promise.all([
    loadBhwDashboard(),
    loadBhwVisitList(),
    loadBhwReferralsList(),
  ]);
}

window.loadBhwWorkspace = loadBhwWorkspace;
window.loadBhwVisitList = loadBhwVisitList;
window.loadBhwReferralsList = loadBhwReferralsList;

// ── RHU dashboard (admin workspace) ──────────────────────────────────────────

/**
 * Fetch /api/rhu/dashboard and populate the RHU admin stat cards.
 *
 * The RHU admin is routed to the existing #page-admin workspace on sign-in.
 * This function fills in the four metric cards in #admin-overview and, if a
 * richer RHU stats grid is present (#rhuStatsGrid), populates that too.
 */
async function loadRhuDashboard() {
  try {
    const data = await _apiGet('/api/rhu/dashboard');

    // ── Basic overview metrics ────────────────────────────────────────────────
    // These target the new rhu-specific stat elements (injected via injectRhuStats)
    if (data.registeredPrenatal != null)
      _setText('rhuPrenatalCount', data.registeredPrenatal);
    if (data.registeredPostpartum != null)
      _setText('rhuPostpartumCount', data.registeredPostpartum);
    if (data.underPrenatalMonitoring != null)
      _setText('rhuMonitoringCount', data.underPrenatalMonitoring);
    if (data.postpartumFollowUp != null)
      _setText('rhuFollowUpCount', data.postpartumFollowUp);
    if (data.openFlags != null)
      _setText('rhuOpenFlagsCount', data.openFlags);
    if (data.overdueReferrals != null)
      _setText('rhuOverdueReferrals', data.overdueReferrals);

    // Referrals by status
    if (data.referralsByStatus) {
      _setText('rhuRefPending',      data.referralsByStatus.pending      || 0);
      _setText('rhuRefAcknowledged', data.referralsByStatus.acknowledged || 0);
      _setText('rhuRefAttended',     data.referralsByStatus.attended      || 0);
      _setText('rhuRefClosed',       data.referralsByStatus.closed       || 0);
    }

  } catch (err) {
    console.warn('RHU dashboard load failed:', err);
  }
}

window.loadRhuDashboard = loadRhuDashboard;

/**
 * Inject the RHU stats grid into the admin overview section and trigger a load.
 * Called once when an rhu_admin signs in (applyStaffIdentity already sets the
 * workspace to 'admin'; this adds the RHU-specific data panel).
 */
function injectRhuStats() {
  const overviewPanel = document.getElementById('admin-overview');
  if (!overviewPanel) return;

  // Only inject once.
  if (document.getElementById('rhuStatsPanel')) {
    loadRhuDashboard();
    return;
  }

  const panel = document.createElement('section');
  panel.id = 'rhuStatsPanel';
  panel.className = 'admin-card rhu-stats-card';
  panel.innerHTML = `
    <header class="admin-card-heading">
      <div>
        <h2>RHU maternal health overview</h2>
        <p>Live counts from the database</p>
      </div>
      <button class="admin-link-button" type="button" onclick="loadRhuDashboard()">
        Refresh <i class="fa-solid fa-rotate"></i>
      </button>
    </header>
    <div class="stats-row admin-stats rhu-stats-grid">
      <article class="admin-metric">
        <p class="admin-metric-label">REGISTERED PRENATAL</p>
        <p class="admin-metric-value" id="rhuPrenatalCount">—</p>
        <p class="admin-metric-meta">Pregnant mothers</p>
      </article>
      <article class="admin-metric">
        <p class="admin-metric-label">REGISTERED POSTPARTUM</p>
        <p class="admin-metric-value" id="rhuPostpartumCount">—</p>
        <p class="admin-metric-meta">Postpartum mothers</p>
      </article>
      <article class="admin-metric">
        <p class="admin-metric-label">PRENATAL MONITORING</p>
        <p class="admin-metric-value" id="rhuMonitoringCount">—</p>
        <p class="admin-metric-meta">Active prenatal visits</p>
      </article>
      <article class="admin-metric">
        <p class="admin-metric-label">POSTPARTUM FOLLOW-UP</p>
        <p class="admin-metric-value" id="rhuFollowUpCount">—</p>
        <p class="admin-metric-meta">Open flag or referral</p>
      </article>
      <article class="admin-metric admin-metric-alert">
        <p class="admin-metric-label">OPEN FLAGS</p>
        <p class="admin-metric-value" id="rhuOpenFlagsCount">—</p>
        <p class="admin-metric-meta">Requires professional review</p>
      </article>
      <article class="admin-metric admin-metric-alert">
        <p class="admin-metric-label">OVERDUE REFERRALS</p>
        <p class="admin-metric-value" id="rhuOverdueReferrals">—</p>
        <p class="admin-metric-meta">Not acknowledged in time</p>
      </article>
    </div>
    <div class="rhu-referral-status-row">
      <p class="rhu-referral-label">Referrals by status:</p>
      <span class="rhu-ref-badge">
        <strong id="rhuRefPending">—</strong> pending
      </span>
      <span class="rhu-ref-badge">
        <strong id="rhuRefAcknowledged">—</strong> acknowledged
      </span>
      <span class="rhu-ref-badge">
        <strong id="rhuRefAttended">—</strong> attended
      </span>
      <span class="rhu-ref-badge">
        <strong id="rhuRefClosed">—</strong> closed
      </span>
    </div>
  `;

  // Insert before the first admin card inside the overview panel.
  const firstCard = overviewPanel.querySelector('.admin-card');
  if (firstCard) {
    overviewPanel.insertBefore(panel, firstCard);
  } else {
    overviewPanel.appendChild(panel);
  }

  loadRhuDashboard();
}

window.injectRhuStats = injectRhuStats;

// ── Automatic refresh when BHW page becomes active ────────────────────────────
// sync-ui.js re-dispatches showPage() as a 'pagechange: bhw' DOM event.
document.addEventListener('pagechange: bhw', function () {
  if (typeof loadBhwWorkspace === 'function') loadBhwWorkspace();
});

// Also refresh when the admin/rhu page becomes active.
document.addEventListener('pagechange: admin', function () {
  // Only if the rhu stats panel is already injected (rhu_admin user).
  if (document.getElementById('rhuStatsPanel')) {
    loadRhuDashboard();
  }
});
