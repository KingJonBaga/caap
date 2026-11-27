/**
 * admin.js
 * Admin workspace: panel switching, account management, audit log, reports.
 */

let accountRoleFilter = 'all';

function showAdminPanel(name, btn) {
  document.querySelectorAll('.admin-panel').forEach(panel => panel.classList.remove('active'));
  document.getElementById('admin-' + name).classList.add('active');
  document.querySelectorAll('[data-admin-panel]').forEach(item => item.classList.remove('active'));
  const activeButton = btn || document.querySelector('[data-admin-panel="' + name + '"]');
  if (activeButton) activeButton.classList.add('active');
  window.scrollTo(0, 0);
  if (name === 'accounts') loadAccounts();
  if (name === 'overview' && typeof loadAdminOverview === 'function') loadAdminOverview();
}

const ACCOUNT_ROLE_LABELS = {
  admin: 'Administrator',
  provider: 'Care provider',
  nurse: 'Nurse',
  caretaker: 'Caretaker',
  patient: 'Patient'
};

function accountInitials(name) {
  return name.split(/\s+/).filter(Boolean).map(word => word[0].toUpperCase()).slice(0, 2).join('');
}

function formatAccountDate(value) {
  const date = new Date(String(value).replace(' ', 'T'));
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
}

function filterAccounts() {
  const term = document.getElementById('accountSearch').value.trim().toLowerCase();
  const rows = [...document.querySelectorAll('#accountRows tr[data-role]')];
  let visibleCount = 0;
  rows.forEach(row => {
    const matchesQuery = (row.textContent + ' ' + row.dataset.accountId).toLowerCase().includes(term);
    const matchesRole = accountRoleFilter === 'all' || row.dataset.role === accountRoleFilter;
    row.hidden = !matchesQuery || !matchesRole;
    if (!row.hidden) visibleCount++;
  });
  document.getElementById('accountCount').textContent =
    visibleCount + ' of ' + rows.length + ' accounts shown';
}

function setAccountRoleFilter(role, button) {
  accountRoleFilter = role;
  document.querySelectorAll('[data-account-filter]').forEach(filterButton => {
    const selected = filterButton === button;
    filterButton.classList.toggle('active', selected);
    filterButton.setAttribute('aria-pressed', String(selected));
  });
  filterAccounts();
}

function setAccountsMessage(text) {
  const body = document.getElementById('accountRows');
  body.replaceChildren();
  const row = document.createElement('tr');
  const cell = document.createElement('td');
  cell.colSpan = 5;
  cell.className = 'accounts-empty';
  cell.textContent = text;
  row.appendChild(cell);
  body.appendChild(row);
  document.getElementById('accountCount').textContent = '';
}

function buildAccountRow(account) {
  const row = document.createElement('tr');
  row.dataset.role = account.role;
  row.dataset.accountId = account.accountId;
  row.dataset.id = account.id;
  row.dataset.name = account.name;
  row.dataset.email = account.email;
  row.dataset.joined = formatAccountDate(account.created_at);
  row.dataset.status = account.status;

  const identityCell = document.createElement('td');
  const identity = document.createElement('div');
  identity.className = 'account-identity';
  const avatar = document.createElement('span');
  avatar.className = 'account-avatar';
  avatar.textContent = accountInitials(account.name);
  const text = document.createElement('div');
  const strong = document.createElement('strong');
  strong.textContent = account.name;
  const email = document.createElement('span');
  email.textContent = account.email;
  text.append(strong, email);
  identity.append(avatar, text);
  identityCell.appendChild(identity);

  const roleCell = document.createElement('td');
  roleCell.textContent = ACCOUNT_ROLE_LABELS[account.role] || account.role;
  const joinedCell = document.createElement('td');
  joinedCell.textContent = row.dataset.joined;

  const statusCell = document.createElement('td');
  const status = document.createElement('span');
  const suspended = account.status === 'suspended';
  status.className = 'account-status ' + (suspended ? 'suspended' : 'active');
  status.textContent = suspended ? 'Suspended' : 'Active';
  statusCell.appendChild(status);

  const actionsCell = document.createElement('td');
  const actions = document.createElement('div');
  actions.className = 'account-actions';
  const view = document.createElement('button');
  view.type = 'button';
  view.className = 'account-view-button';
  view.setAttribute('aria-label', 'View ' + account.name + ' account');
  view.innerHTML = '<i class="fa-regular fa-eye"></i>';
  view.addEventListener('click', () => viewAccount(view));
  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.className = 'account-state-button';
  toggle.textContent = suspended ? 'Activate' : 'Suspend';
  toggle.addEventListener('click', () => toggleAccount(toggle));
  actions.append(view, toggle);
  actionsCell.appendChild(actions);

  row.append(identityCell, roleCell, joinedCell, statusCell, actionsCell);
  return row;
}

async function loadAccounts() {
  if (!document.getElementById('accountRows')) return;
  setAccountsMessage('Loading accounts…');
  try {
    const response = await fetch('/api/accounts', { cache: 'no-store' });
    if (response.status === 401 || response.status === 403) {
      setAccountsMessage('Sign in as an administrator to see accounts.');
      return;
    }
    if (!response.ok) throw new Error('Could not load accounts.');
    const { accounts } = await response.json();
    const body = document.getElementById('accountRows');
    body.replaceChildren(...accounts.map(buildAccountRow));
    filterAccounts();
  } catch (error) {
    setAccountsMessage("Couldn't load accounts. Check that the server is running.");
  }
}

function viewAccount(button) {
  const row = button.closest('tr');
  document.getElementById('accountDetailName').textContent = row.dataset.name;
  document.getElementById('accountDetailId').textContent = row.dataset.accountId;
  document.getElementById('accountDetailEmail').textContent = row.dataset.email;
  document.getElementById('accountDetailRole').textContent = row.cells[1].textContent;
  document.getElementById('accountDetailJoined').textContent = row.dataset.joined;
  document.getElementById('accountDetailStatus').textContent = row.dataset.status === 'suspended' ? 'Suspended' : 'Active';
  document.getElementById('accountDetailDialog').showModal();
}

async function toggleAccount(button) {
  const row = button.closest('tr');
  const nextStatus = row.dataset.status === 'suspended' ? 'active' : 'suspended';
  button.disabled = true;
  try {
    const response = await fetch(`/api/accounts/${row.dataset.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: nextStatus })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showAdminToast(data.error || 'Could not change this account.');
      button.disabled = false;
      return;
    }
    showAdminToast(row.dataset.name + (nextStatus === 'suspended'
      ? ' is suspended and can no longer sign in.'
      : ' is active again.'));
    await loadAccounts();
  } catch (error) {
    button.disabled = false;
    showAdminToast("Couldn't change this account. Check that the server is running.");
  }
}

function openInviteDialog() {
  document.getElementById('inviteError').textContent = '';
  document.getElementById('inviteForm').reset();
  document.getElementById('inviteDialog').showModal();
  document.getElementById('inviteName').focus();
}

async function submitInvite(event) {
  event.preventDefault();
  const errorEl = document.getElementById('inviteError');
  errorEl.textContent = '';
  try {
    const response = await fetch('/api/accounts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: document.getElementById('inviteName').value,
        email: document.getElementById('inviteEmail').value,
        role: document.getElementById('inviteRole').value
      })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      errorEl.textContent = data.error || 'Could not create the account.';
      return;
    }
    document.getElementById('inviteDialog').close();
    await loadAccounts();
    showAdminToast('Account created for ' + data.account.email + '. They can set their password with "Forgot password?" on the sign-in page.');
  } catch (error) {
    errorEl.textContent = "Can't reach the server. The account was not created.";
  }
}

function setAuditFilter(category, button) {
  document.querySelectorAll('[data-audit-filter]').forEach(filterButton => {
    const selected = filterButton === button;
    filterButton.classList.toggle('active', selected);
    filterButton.setAttribute('aria-pressed', String(selected));
  });
  const events = [...document.querySelectorAll('#auditEventStream .audit-event')];
  let visibleCount = 0;
  events.forEach(event => {
    event.hidden = category !== 'all' && event.dataset.category !== category;
    if (!event.hidden) visibleCount++;
  });
  document.getElementById('auditEventCount').textContent =
    visibleCount + (visibleCount === 1 ? ' event' : ' events') + ' in this view';
}

function appendAccountAudit(accountName, action) {
  const category = 'accounts';
  const event = document.createElement('article');
  event.className = 'audit-event';
  event.dataset.category = category;
  const marker = document.createElement('span');
  marker.className = 'audit-timeline-marker';
  const details = document.createElement('div');
  const heading = document.createElement('h3');
  heading.append(document.createTextNode('Account status updated '));
  const label = document.createElement('span');
  label.className = 'audit-category';
  label.textContent = 'ACCOUNTS';
  heading.append(label);
  const description = document.createElement('p');
  description.textContent = accountName + ' moved to ' + action;
  const time = document.createElement('time');
  time.textContent = 'Just now, ' + new Intl.DateTimeFormat(undefined, {
    hour: '2-digit', minute: '2-digit'
  }).format(new Date());
  details.append(heading, description, time);
  event.append(marker, details);
  document.getElementById('auditEventStream').prepend(event);
  const currentFilter = document.querySelector('[data-audit-filter].active')?.dataset.auditFilter || 'all';
  setAuditFilter(currentFilter, document.querySelector('[data-audit-filter].active'));
}

function exportAuditLog() {
  const rows = [['Event', 'Category', 'Details', 'Time']];
  document.querySelectorAll('#auditEventStream .audit-event').forEach(event => {
    rows.push([
      event.querySelector('h3').childNodes[0].textContent.trim(),
      event.dataset.category,
      event.querySelector('p').textContent,
      event.querySelector('time').textContent
    ]);
  });
  const csv = rows.map(row => row.map(value => '"' + value.replace(/"/g, '""') + '"').join(',')).join('\r\n');
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'nurtura-audit-log.csv';
  link.click();
  URL.revokeObjectURL(url);
  showAdminToast('Audit log exported.');
}

// Builds the care summary from the live RHU dashboard (task 9.1), not fixed numbers.
async function downloadCareSummary() {
  let data;
  try {
    const response = await fetch('/api/rhu/dashboard', { cache: 'no-store' });
    if (!response.ok) throw new Error('Could not load the dashboard.');
    data = await response.json();
  } catch (error) {
    showAdminToast("Couldn't build the care summary. Check that the server is running.");
    return;
  }
  const statuses = data.referralsByStatus || {};
  const rows = [
    ['Measure', 'Value', 'Source'],
    ['Registered prenatal mothers', data.registeredPrenatal, 'Database'],
    ['Registered postpartum mothers', data.registeredPostpartum, 'Database'],
    ['Under prenatal monitoring', data.underPrenatalMonitoring, 'Database'],
    ['Postpartum follow-up', data.postpartumFollowUp, 'Database'],
    ['Open flags for review', data.openFlags, 'Database'],
    ['Referrals pending', statuses.pending || 0, 'Database'],
    ['Referrals acknowledged', statuses.acknowledged || 0, 'Database'],
    ['Referrals attended', statuses.attended || 0, 'Database'],
    ['Referrals closed', statuses.closed || 0, 'Database'],
    ['Overdue referrals', data.overdueReferrals, 'Database'],
    ['Data note', 'Counts are live from the database; small groups are not broken down further.', '']
  ];
  const csv = rows.map(row => row.map(value => '"' + String(value ?? '').replace(/"/g, '""') + '"').join(',')).join(String.fromCharCode(13, 10));
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'nurtura-care-summary.csv';
  link.click();
  URL.revokeObjectURL(url);
  showAdminToast('Care summary downloaded.');
}
