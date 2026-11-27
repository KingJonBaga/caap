/**
 * admin-overview.js
 * Fills the administrator overview from live data: /api/accounts and /api/audit.
 * Nothing on this page is a sample number.
 */

'use strict';

const OV_ACTION_LABELS = {
  login: 'Signed in',
  logout: 'Signed out',
  login_failed: 'Failed sign-in',
  login_suspended: 'Sign-in refused (suspended)',
  account_invited: 'Account invited',
  account_suspended: 'Account suspended',
  account_activated: 'Account reactivated',
  register: 'Patient registered',
  password_reset_requested: 'Password reset requested',
  password_reset_completed: 'Password reset completed',
  patient_view: 'Viewed a patient record',
  patient_update: 'Updated a patient record',
  note_added: 'Added a care note',
  visit_created: 'Created a visit',
  visit_updated: 'Updated a visit',
  message_sent: 'Sent a care message',
  report_exported: 'Exported a report',
};

function _ovText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function _ovEmpty(text) {
  const p = document.createElement('p');
  p.className = 'ov-empty';
  p.textContent = text;
  return p;
}

function _ovSetGreeting() {
  const hour = new Date().getHours();
  const part = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  const nameEl = document.querySelector('#page-admin .user-name');
  const firstName = nameEl ? nameEl.textContent.trim().split(/\s+/)[0] : '';
  _ovText('ovGreeting', firstName ? `${part}, ${firstName}.` : `${part}.`);
  _ovText('ovDate', new Date().toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'long', year: 'numeric' }));
}

function _ovRenderRoles(accounts) {
  const list = document.getElementById('ovRoles');
  if (!list) return;
  const counts = {};
  accounts.forEach(account => { counts[account.role] = (counts[account.role] || 0) + 1; });
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (!entries.length) {
    list.replaceChildren(_ovEmpty('No accounts yet.'));
    return;
  }
  const largest = entries[0][1];
  list.replaceChildren(...entries.map(([role, count]) => {
    const row = document.createElement('div');
    row.className = 'ov-role-row';
    const label = document.createElement('div');
    label.className = 'ov-role-label';
    const name = document.createElement('span');
    name.textContent = (typeof ACCOUNT_ROLE_LABELS !== 'undefined' && ACCOUNT_ROLE_LABELS[role]) || role;
    const number = document.createElement('strong');
    number.textContent = count;
    label.append(name, number);
    const track = document.createElement('div');
    track.className = 'ov-role-track';
    const fill = document.createElement('span');
    fill.style.width = Math.round((count / largest) * 100) + '%';
    track.appendChild(fill);
    row.append(label, track);
    return row;
  }));
}

function _ovRenderActivity(events) {
  const list = document.getElementById('ovActivity');
  if (!list) return;
  if (!events.length) {
    list.replaceChildren(_ovEmpty('Nothing has been logged yet.'));
    return;
  }
  list.replaceChildren(...events.slice(0, 6).map(event => {
    const row = document.createElement('div');
    row.className = 'ov-activity-row';
    const dot = document.createElement('span');
    dot.className = 'ov-activity-dot';
    dot.setAttribute('aria-hidden', 'true');
    const text = document.createElement('div');
    const action = document.createElement('p');
    action.className = 'ov-activity-action';
    action.textContent = OV_ACTION_LABELS[event.action] || event.action.replace(/_/g, ' ');
    const who = document.createElement('p');
    who.className = 'ov-activity-meta';
    who.textContent = (event.user_email || 'Unknown') + ' · ' + (event.at || '');
    text.append(action, who);
    row.append(dot, text);
    return row;
  }));
}

async function loadAdminOverview() {
  if (!document.getElementById('admin-overview')) return;
  _ovSetGreeting();
  try {
    const [accountsRes, auditRes] = await Promise.all([
      fetch('/api/accounts', { cache: 'no-store' }),
      fetch('/api/audit', { cache: 'no-store' }),
    ]);
    if (!accountsRes.ok || !auditRes.ok) throw new Error('Could not load the overview.');
    const { accounts } = await accountsRes.json();
    const { events } = await auditRes.json();

    const suspended = accounts.filter(a => a.status === 'suspended').length;
    const today = new Date().toISOString().slice(0, 10);
    const signinsToday = events.filter(e => e.action === 'login' && String(e.at).startsWith(today)).length;
    const invited = events.filter(e => e.action === 'account_invited').length;
    const suspensions = events.filter(e => e.action === 'account_suspended').length;

    _ovText('ovTotal', accounts.length);
    _ovText('ovActive', accounts.length - suspended);
    _ovText('ovSuspended', suspended);
    _ovText('ovSignins', signinsToday);
    _ovText('ovInvited', invited);
    _ovText('ovSuspendEvents', suspensions);
    _ovText('ovEvents', events.length);
    _ovRenderRoles(accounts);
    _ovRenderActivity(events);
  } catch (error) {
    ['ovRoles', 'ovActivity'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.replaceChildren(_ovEmpty("Couldn't load this. Check that the server is running."));
    });
  }
}

window.loadAdminOverview = loadAdminOverview;
