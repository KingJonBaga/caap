/**
 * provider.js
 * Care team workspace: panel switching, notifications, schedule, caseload,
 * patient summaries, records, and patient filtering.
 */

/* ── Panel & notifications ─────────────────────────── */

function showProviderPanel(name, btn) {
  document.querySelectorAll('.provider-panel').forEach(panel => panel.classList.remove('active'));
  const panel = document.getElementById('provider-' + name);
  if (!panel) throw new Error('Care team panel not found: ' + name);
  panel.classList.add('active');
  document.querySelectorAll('[data-provider-panel]').forEach(item => item.classList.remove('active'));
  const activeButton = btn || document.querySelector('[data-provider-panel="' + name + '"]');
  if (activeButton) activeButton.classList.add('active');
  window.scrollTo(0, 0);
}

function toggleProviderNotifications() {
  const panel = document.getElementById('providerNotifications');
  const trigger = document.getElementById('providerNotificationTrigger');
  panel.hidden = !panel.hidden;
  trigger.setAttribute('aria-expanded', String(!panel.hidden));
}

function updateProviderNotificationCount() {
  const unread = document.querySelectorAll('#providerNotificationList [data-unread="true"]').length;
  const badge = document.getElementById('providerNotificationCount');
  const trigger = document.getElementById('providerNotificationTrigger');
  const message = document.getElementById('providerNotificationUnread');
  badge.textContent = unread;
  badge.hidden = unread === 0;
  trigger.setAttribute('aria-label', unread ? 'Notifications, ' + unread + ' unread' : 'Notifications, no unread messages');
  message.textContent = unread + (unread === 1 ? ' unread message' : ' unread messages');
}

function markProviderNotificationRead(notification) {
  notification.dataset.unread = 'false';
  notification.classList.remove('unread');
  notification.querySelector('.provider-notification-unread-dot').setAttribute('aria-hidden', 'true');
  updateProviderNotificationCount();
}

function markAllProviderNotificationsRead() {
  document.querySelectorAll('#providerNotificationList [data-unread="true"]').forEach(markProviderNotificationRead);
}

/* ── Schedule data & rendering ─────────────────────── */

const BOARD_WEEK_START = '2026-11-30'; // Monday shown on the week board (matches server/visits.js)

function addBoardDays(isoDate, days) {
  const [y, m, d] = isoDate.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10);
}

function formatBoardDate(isoDate) {
  const [y, m, d] = isoDate.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}

const providerDays = [
  { id: 'mon', short: 'Mon', label: 'Monday',    offset: 0, slots: 8 },
  { id: 'tue', short: 'Tue', label: 'Tuesday',   offset: 1, slots: 8 },
  { id: 'wed', short: 'Wed', label: 'Wednesday', offset: 2, slots: 8 },
  { id: 'thu', short: 'Thu', label: 'Thursday',  offset: 3, slots: 8, nearCapacity: true },
  { id: 'fri', short: 'Fri', label: 'Friday',    offset: 4, slots: 8 }
];
providerDays.forEach(day => {
  day.date = formatBoardDate(addBoardDays(BOARD_WEEK_START, day.offset));
});

// Visits for the board week, loaded from the database by loadProviderSchedule().
const providerAppointments = [];

let providerSelectedDay = 'mon';
let providerScheduleView = 'week';

function schMinutes(time) {
  const m = time.match(/(\d+):(\d+)/);
  return m ? Number(m[1]) * 60 + Number(m[2]) : 0;
}

function renderProviderSchedule() {
  renderSchWeek();
  renderSchDaySelectors();
  renderSchDay();
  updateSchLegend();
}

function renderSchWeek() {
  const grid = document.getElementById('schWeekGrid');
  if (!grid) return;
  grid.innerHTML = providerDays.map(day => {
    const appts = providerAppointments
      .filter(a => a.day === day.id)
      .sort((a, b) => schMinutes(a.time) - schMinutes(b.time));
    const count = appts.length;
    const pct = Math.round((count / day.slots) * 100);
    const dayLabel = (day.today ? 'TODAY' : day.short.toUpperCase());
    return `<div class="sch-day-col${day.today ? ' sch-day-col--today' : ''}${day.nearCapacity ? ' sch-day-col--warn' : ''}" data-day="${day.id}">
      <div class="sch-day-col-head">
        <div class="sch-day-col-label">
          <span class="sch-day-short">${dayLabel}</span>
          <span class="sch-day-date">${day.date}</span>
        </div>
        <span class="sch-day-count">${count}</span>
      </div>
      <div class="sch-day-bar"><div class="sch-day-bar-fill${day.nearCapacity ? ' sch-day-bar-fill--warn' : ''}" style="width:${pct}%"></div></div>
      <p class="sch-day-slots">${count} of ${day.slots} slots <button class="sch-day-view-btn" onclick="switchSchToDay('${day.id}')">Day view</button></p>
      <div class="sch-appt-list">
        ${appts.map(a => renderSchCard(a)).join('')}
      </div>
    </div>`;
  }).join('');
}

function renderSchCard(a) {
  const isCompleted = a.status === 'completed';
  const initials = a.name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();
  const moveOpts = providerDays
    .filter(d => d.id !== a.day)
    .map(d => `<button type="button" onclick="moveSchAppt('${a.id}','${d.id}')">${d.label}, ${d.date}</button>`)
    .join('');
  return `<div class="sch-appt-card${isCompleted ? ' sch-appt-card--done' : ''}" data-appt-id="${a.id}" draggable="true" ondragstart="schDragStart(event,'${a.id}')" ondragover="event.preventDefault()" ondrop="schDrop(event,'${a.day}')">
    <div class="sch-appt-top">
      <span class="sch-appt-time">${a.time}</span>
      <div class="sch-appt-move-wrap">
        <button class="sch-appt-move-btn" type="button" aria-label="Move ${a.name}" onclick="toggleSchMoveMenu(this)"><i class="fa-solid fa-arrow-right-arrow-left"></i></button>
        <div class="sch-appt-move-menu" hidden><strong>Move to</strong>${moveOpts}</div>
      </div>
    </div>
    <p class="sch-appt-name">${a.name}</p>
    <p class="sch-appt-visit">${a.visit}</p>
    <div class="sch-appt-foot">
      <span class="sch-appt-room">${a.room}</span>
      ${isCompleted ? '<span class="sch-appt-badge sch-appt-badge--done">Completed</span>' : ''}
    </div>
  </div>`;
}

function renderSchDaySelectors() {
  const wrap = document.getElementById('providerDaySelectors');
  if (!wrap) return;
  wrap.innerHTML = providerDays.map(day => {
    const count = providerAppointments.filter(a => a.day === day.id).length;
    return `<button type="button" class="sch-day-sel-btn${day.id === providerSelectedDay ? ' active' : ''}" onclick="selectProviderDay('${day.id}')">
      <strong>${day.short}</strong><span>${day.date}</span><small>${count} visit${count !== 1 ? 's' : ''}</small>
    </button>`;
  }).join('');
}

function renderSchDay() {
  const wrap = document.getElementById('schDayCards');
  if (!wrap) return;
  const day = providerDays.find(d => d.id === providerSelectedDay);
  const appts = providerAppointments
    .filter(a => a.day === providerSelectedDay)
    .sort((a, b) => schMinutes(a.time) - schMinutes(b.time));
  wrap.innerHTML = `<div class="sch-day-heading"><h3>${day.label}, ${day.date}</h3><span>${appts.length} visit${appts.length !== 1 ? 's' : ''}</span></div>
    <div class="sch-day-cards-grid">${appts.map(a => renderSchCard(a)).join('') || '<p class="sch-empty">No visits scheduled for this day.</p>'}</div>`;
}

function updateSchLegend() {
  const warn = providerDays.find(d => d.nearCapacity);
  const note = document.getElementById('schCapacityNote');
  if (note && warn) note.textContent = `${warn.label} is near capacity — consider shifting routine reviews to Friday morning.`;
  if (note && !warn) note.textContent = '';
}

function selectProviderDay(dayId) {
  if (!providerDays.some(d => d.id === dayId)) throw new Error('Unknown schedule day: ' + dayId);
  providerSelectedDay = dayId;
  renderSchDaySelectors();
  renderSchDay();
}

function switchSchToDay(dayId) {
  providerSelectedDay = dayId;
  setProviderScheduleView('day', document.getElementById('providerDayViewButton'));
}

function setProviderScheduleView(view, button) {
  providerScheduleView = view;
  document.getElementById('providerWeekBoard').hidden = view !== 'week';
  document.getElementById('providerDayList').hidden  = view !== 'day';
  document.getElementById('providerWeekViewButton').classList.toggle('active', view === 'week');
  document.getElementById('providerDayViewButton').classList.toggle('active', view === 'day');
  document.getElementById('providerWeekViewButton').setAttribute('aria-pressed', String(view === 'week'));
  document.getElementById('providerDayViewButton').setAttribute('aria-pressed', String(view === 'day'));
  if (view === 'day') { renderSchDaySelectors(); renderSchDay(); }
}

function toggleSchMoveMenu(button) {
  const menu = button.parentElement.querySelector('.sch-appt-move-menu');
  const opening = menu.hidden;
  document.querySelectorAll('.sch-appt-move-menu').forEach(m => m.hidden = true);
  menu.hidden = !opening;
}

/* Drag & drop */
let schDragId = null;
function schDragStart(event, apptId) { schDragId = apptId; event.dataTransfer.effectAllowed = 'move'; }
function schDrop(event, dayId) {
  event.preventDefault();
  if (schDragId) { moveSchAppt(schDragId, dayId); schDragId = null; }
}

/* Legacy aliases used by old HTML/JS references */
function renderProviderTable() {}
function renderProviderRow() {}
function toggleProviderMoveMenu(b) { toggleSchMoveMenu(b); }
function moveProviderAppointment(i, d) { moveSchAppt(providerAppointments[i]?.id, d); }


/* ── Patient summaries data & dialog ───────────────── */

const providerPatientSummaries = {
  'MH-2025-0148': { name: 'Maria Santos', displayId: 'PT-1041', stage: '28w 2d · Low risk', gestation: '28w 2d', bloodPressure: '114/71', weight: '66.4 kg', update: 'Weekly wellness check submitted', appointment: '27 Oct · 10:30', status: 'Up to date', flag: '', note: 'Glucose tolerance normal. Discuss birth plan at next visit.', history: 'O+ · Penicillin allergy · Mild seasonal asthma' },
  'MH-2025-0162': { name: 'Leah Cruz', displayId: 'MH-2025-0162', stage: '32 weeks · Third trimester', gestation: '32 weeks', bloodPressure: '126/82', weight: '70.2 kg', update: 'Blood pressure check-in submitted', appointment: 'Monday · 10:30 AM · Blood pressure follow-up', status: 'Needs review', flag: 'Blood pressure follow-up', note: 'Sample check-in submitted for care-team review.', history: 'No additional history shown in this sample' },
  'MH-2025-0109': { name: 'Janelle Reyes', displayId: 'MH-2025-0109', stage: '2 weeks postpartum', gestation: 'Postpartum · week 2', bloodPressure: '116/74', weight: '67.1 kg', update: 'Postpartum questionnaire received', appointment: 'Monday · 1:00 PM · Postpartum review', status: 'Needs review', flag: 'Postpartum follow-up', note: 'Sample postpartum questionnaire received.', history: 'No additional history shown in this sample' },
  'PT-1038': { name: 'Amara Bello', displayId: 'PT-1038', stage: '34w 5d · Elevated BP', gestation: '34w 5d', bloodPressure: '138/88', weight: '72.1 kg', update: 'Blood pressure check-in submitted', appointment: '26 Oct · 09:00', status: 'Needs review', flag: 'Blood pressure watch', note: 'Repeat BP check in one week. Monitor for headache and visual changes.', history: 'Sample record · no additional history shown' },
  'PT-1029': { name: 'Priya Raman', displayId: 'PT-1029', stage: '12w 4d · Low risk', gestation: '12w 4d', bloodPressure: '108/66', weight: '59.4 kg', update: 'Routine check-in received', appointment: '29 Oct · 08:45', status: 'Up to date', flag: '', note: 'Routine prenatal check-in reviewed. Discuss questions and concerns at the next visit.', history: 'Sample record · no additional history shown' },
  'PT-1046': { name: 'Hannah Cole', displayId: 'PT-1046', stage: 'Postpartum · week 3', gestation: 'Postpartum · week 3', bloodPressure: '118/76', weight: '68.9 kg', update: 'Postpartum follow-up requested', appointment: '02 Nov · 11:00', status: 'Needs review', flag: 'Postpartum follow-up', note: 'Postpartum follow-up requested. Discuss feeding support and recovery at the next visit.', history: 'Sample record · no additional history shown' },
  'PT-1052': { name: 'Sofia Marchetti', displayId: 'PT-1052', stage: '30w 1d · Low risk', gestation: '30w 1d', bloodPressure: '112/70', weight: '64.8 kg', update: 'Wellness follow-up', appointment: '28 Oct · 14:15', status: 'Up to date', flag: '', note: 'Wellness check-in reviewed. Discuss birth preferences at the next visit.', history: 'Sample record · no additional history shown' },
  'PT-1041': { name: 'Maya Thompson', displayId: 'PT-1041', stage: '26 weeks · Second trimester', gestation: '26 weeks', bloodPressure: '110/68', weight: '62.0 kg', update: 'Routine prenatal update', appointment: 'Friday · 10:00 AM · Routine prenatal visit', status: 'Up to date', flag: '', note: '', history: 'Sample record · no additional history shown' }
};

// Load patient records from the local database (server/server.py). If the server is not
// running, the built-in sample data above stays in use.
async function loadProviderPatientsFromDatabase() {
  try {
    const response = await fetch('/api/patients', { cache: 'no-store' });
    if (!response.ok) return;
    const { patients } = await response.json();
    patients.forEach(({ id, updatedAt, ...patient }) => {
      providerPatientSummaries[id] = { ...providerPatientSummaries[id], ...patient };
    });
  } catch (error) {
    // Database server unavailable: keep the sample data.
  }
}
loadProviderPatientsFromDatabase();

function openProviderSummary(button) {
  const patientCard = button.closest('[data-patient-id]');
  if (!patientCard) throw new Error('Patient summary card not found.');
  openProviderSummaryById(patientCard.dataset.patientId);
}

function openProviderSummaryById(patientId) {
  const summary = providerPatientSummaries[patientId];
  if (!summary) throw new Error('Patient summary not found: ' + patientId);
  document.getElementById('providerSummaryName').textContent = summary.name;
  document.getElementById('providerSummaryId').textContent = summary.displayId || patientId;
  document.getElementById('providerSummaryStage').textContent = summary.stage;
  document.getElementById('providerSummaryGestation').textContent = summary.gestation;
  document.getElementById('providerSummaryBloodPressure').textContent = summary.bloodPressure;
  document.getElementById('providerSummaryWeight').textContent = summary.weight;
  document.getElementById('providerSummaryAppointment').textContent = summary.appointment;
  const note = document.getElementById('providerSummaryClinicalNote');
  const noteWrap = document.getElementById('providerSummaryClinicalNoteWrap');
  note.textContent = summary.note;
  noteWrap.hidden = !summary.note;
  const flag = document.getElementById('providerSummaryFlag');
  flag.textContent = summary.flag;
  flag.hidden = !summary.flag;
  const card = document.querySelector('#providerCaseloadGrid [data-patient-id="' + patientId + '"]');
  const tags = document.getElementById('providerSummaryTags');
  tags.replaceChildren();
  if (card) {
    card.querySelectorAll('.caseload-tags span').forEach(tag => {
      const chip = document.createElement('span');
      chip.textContent = tag.textContent;
      tags.appendChild(chip);
    });
  }
  document.getElementById('providerSummaryFullRecord').dataset.patientId = patientId;
  document.getElementById('providerSummaryFullRecord').onclick = openProviderFullRecord;
  document.getElementById('providerSummaryDialog').showModal();
}

function openProviderFullRecord() {
  const patientId = this.dataset.patientId;
  const patientElement = document.querySelector('#providerCaseloadGrid [data-patient-id="' + patientId + '"]') ||
    document.querySelector('#provider-patients #patientRows [data-patient-id="' + patientId + '"]');
  if (!patientElement) throw new Error('Patient record entry not found: ' + patientId);
  document.getElementById('providerSummaryDialog').close();
  showProviderPanel('patients');
  openProviderRecord(patientElement);
}

/* ── Caseload filtering & export ───────────────────── */

function filterProviderCaseload() {
  const searchInput = document.getElementById('caseloadSearch');
  const term = searchInput ? searchInput.value.trim().toLowerCase() : '';
  const activeBtn = document.querySelector('[data-caseload-filter].active');
  const risk = activeBtn ? activeBtn.dataset.caseloadFilter : 'all';

  document.querySelectorAll('.cl2-card').forEach(card => {
    const cardCategory = (card.dataset.filterCategory || '').toLowerCase();
    const cardRisk     = (card.dataset.risk || '').toLowerCase();

    let matchesRisk = false;
    if      (risk === 'all')        matchesRisk = true;
    else if (risk === 'low')        matchesRisk = cardRisk === 'low';
    else if (risk === 'elevated')   matchesRisk = cardCategory === 'elevated';
    else if (risk === 'postpartum') matchesRisk = cardCategory === 'postpartum';

    const matchesSearch = !term || card.textContent.toLowerCase().includes(term);
    card.hidden = !(matchesRisk && matchesSearch);
  });
}

function setCaseloadRiskFilter(risk, button) {
  document.querySelectorAll('[data-caseload-filter]').forEach(f => {
    f.classList.toggle('active', f === button);
  });
  filterProviderCaseload();
}

function exportProviderCaseload() {
  const rows = [['Patient', 'Patient ID', 'Risk level', 'Gestation', 'Latest blood pressure', 'Weight', 'Next appointment']];
  document.querySelectorAll('.cl2-card:not([hidden]), .caseload-card:not([hidden])').forEach(card => {
    const metrics = card.querySelectorAll('.cl2-metrics strong, .caseload-metrics strong');
    const nameEl = card.querySelector('.cl2-name, .caseload-patient-identity h2');
    const idEl = card.querySelector('.cl2-id, .caseload-id');
    const riskEl = card.querySelector('.cl2-risk, .provider-risk');
    rows.push([
      nameEl ? nameEl.textContent : '',
      idEl ? idEl.textContent.split(' · ')[0] : '',
      riskEl ? riskEl.textContent : '',
      metrics[0] ? metrics[0].textContent : '',
      metrics[1] ? metrics[1].textContent : '',
      metrics[2] ? metrics[2].textContent : '',
      metrics[3] ? metrics[3].textContent : ''
    ]);
  });
  const csv = rows.map(row => row.map(value => '"' + value.replace(/"/g, '""') + '"').join(',')).join('\r\n');
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'nurtura-caseload.csv';
  link.click();
  URL.revokeObjectURL(url);
  showToast('Caseload list exported.');
}

/* ── Patient record detail ─────────────────────────── */

function openProviderRecord(button) {
  const patient = button.matches('[data-patient-id]') ? button : button.closest('[data-patient-id]');
  const patientId = patient && patient.dataset.patientId;
  const record = providerPatientSummaries[patientId];
  if (!record) {
    throw new Error('Patient record not found: ' + patientId);
  }
  document.getElementById('providerRecordName').textContent = record.name;
  document.getElementById('providerRecordId').textContent = record.displayId || patientId;
  document.getElementById('providerRecordStage').textContent = record.stage;
  document.getElementById('providerRecordAppointment').textContent = record.appointment;
  document.getElementById('providerRecordUpdate').textContent = record.update;
  document.getElementById('providerRecordHistory').textContent = record.history;
  document.getElementById('providerCareNote').value = '';
  document.getElementById('providerCareNotes').replaceChildren();
  document.getElementById('providerPatientDirectory').hidden = true;
  document.getElementById('providerCaseloadGrid').hidden = true;
  document.querySelector('.cl2-toolbar').hidden = true;
  document.querySelector('.cl2-notes-card').hidden = true;
  document.getElementById('providerRecordDetail').hidden = false;
  document.getElementById('providerRecordName').focus();
}

function closeProviderRecord() {
  document.getElementById('providerRecordDetail').hidden = true;
  document.getElementById('providerPatientDirectory').hidden = true;
  document.getElementById('providerCaseloadGrid').hidden = false;
  document.querySelector('.cl2-toolbar').hidden = false;
  document.querySelector('.cl2-notes-card').hidden = false;
  document.getElementById('caseloadSearch').focus();
}

function reviewPatient(button) {
  const row = button.closest('tr') || button.closest('.followup-item');
  const name = row.querySelector('strong').textContent;
  button.textContent = 'Reviewed';
  button.disabled = true;
  button.classList.add('is-complete');
  const count = document.getElementById('followupCount');
  if (count && Number(count.textContent) > 0) count.textContent = Number(count.textContent) - 1;
  showToast(name + "'s update marked as reviewed on this page only. The change is not saved.");
}

function filterPatients(query) {
  const term = query.trim().toLowerCase();
  document.querySelectorAll('#patientRows tr').forEach(row => {
    row.hidden = !row.textContent.toLowerCase().includes(term);
  });
  if (document.getElementById('caseloadSearch') && document.getElementById('patientSearch')) {
    document.getElementById('caseloadSearch').value = query;
    filterProviderCaseload();
  }
}

/* ── Notification keyboard/click outside handlers ──── */

document.addEventListener('click', event => {
  const wrap = document.getElementById('providerNotificationWrap');
  const panel = document.getElementById('providerNotifications');
  if (!wrap.contains(event.target) && !panel.hidden) {
    panel.hidden = true;
    document.getElementById('providerNotificationTrigger').setAttribute('aria-expanded', 'false');
  }
});

document.addEventListener('keydown', event => {
  const panel = document.getElementById('providerNotifications');
  if (event.key === 'Escape' && !panel.hidden) {
    panel.hidden = true;
    document.getElementById('providerNotificationTrigger').setAttribute('aria-expanded', 'false');
    document.getElementById('providerNotificationTrigger').focus();
  }
});

/* ── Schedule and notes: saved to the database ─────── */

function visitToBoardItem(visit) {
  const day = providerDays.find(d => addBoardDays(BOARD_WEEK_START, d.offset) === visit.starts_on);
  if (!day) return null; // outside the week shown on the board
  return {
    id: visit.id,
    patientId: visit.patient_id,
    name: visit.patient_name,
    day: day.id,
    time: visit.starts_at,
    visit: visit.title,
    room: visit.room || visit.location,
    status: visit.status === 'completed' ? 'completed' : 'scheduled'
  };
}

async function loadProviderSchedule() {
  try {
    const to = addBoardDays(BOARD_WEEK_START, 4);
    const response = await fetch(`/api/visits?from=${BOARD_WEEK_START}&to=${to}`, { cache: 'no-store' });
    if (response.status === 401) {
      showToast('Please sign in to see the schedule.', 'providerToast');
      return;
    }
    if (!response.ok) throw new Error('Could not load the schedule.');
    const { visits } = await response.json();
    const items = visits.map(visitToBoardItem).filter(Boolean);
    providerAppointments.splice(0, providerAppointments.length, ...items);
    renderProviderSchedule();
  } catch (error) {
    showToast("Couldn't load the schedule. Check that the server is running.", 'providerToast');
  }
}

async function moveSchAppt(apptId, dayId) {
  const appointment = providerAppointments.find(x => String(x.id) === String(apptId));
  if (!appointment) throw new Error('Appointment not found: ' + apptId);
  const day = providerDays.find(d => d.id === dayId);
  document.querySelectorAll('.sch-appt-move-menu').forEach(m => m.hidden = true);
  if (appointment.day === dayId) return;
  try {
    const response = await fetch(`/api/visits/${appointment.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ startsOn: addBoardDays(BOARD_WEEK_START, day.offset) })
    });
    if (!response.ok) throw new Error('Save failed');
    appointment.day = dayId;
    renderProviderSchedule();
    showToast('Appointment moved to ' + day.label + ' and saved.');
  } catch (error) {
    showToast("Couldn't move that appointment. Check that the server is running.");
  }
}

// Patients who have a caretaker linked (only Maria Santos for now, see server/caretaker.js).
const CARETAKER_LINKED_PATIENTS = [{ id: 'MH-2025-0148', name: 'Maria Santos' }];

async function populateSchedulePatientList() {
  const select = document.getElementById('schAddPatient');
  if (!select) return;
  try {
    const response = await fetch('/api/patients', { cache: 'no-store' });
    if (!response.ok) throw new Error('Could not load patients.');
    const { patients } = await response.json();
    select.replaceChildren(...patients.map(p => new Option(p.name + ' (' + p.displayId + ')', p.id)));
  } catch (error) {
    select.replaceChildren(new Option('Patients unavailable', ''));
  }
}

function populateCareMessagePatients() {
  const select = document.getElementById('careMessagePatient');
  if (!select) return;
  select.replaceChildren(...CARETAKER_LINKED_PATIENTS.map(p => new Option(p.name + ' (caretaker: Daniel Cruz)', p.id)));
}

function toggleSchAddForm() {
  const panel = document.getElementById('schAddPanel');
  if (!panel) return;
  panel.hidden = !panel.hidden;
  if (!panel.hidden) {
    document.getElementById('schAddDate').value = BOARD_WEEK_START;
    document.getElementById('schAddTime').value = '09:00';
    populateSchedulePatientList();
    document.getElementById('schAddVisit').focus();
  }
}

async function submitSchAppointment(event) {
  event.preventDefault();
  const select = document.getElementById('schAddPatient');
  const errorEl = document.getElementById('schAddError');
  errorEl.textContent = '';
  const patientOption = select.options[select.selectedIndex];
  const body = {
    patientId: select.value,
    patientName: patientOption ? patientOption.textContent.replace(/\s*\(.*\)\s*$/, '') : '',
    startsOn: document.getElementById('schAddDate').value,
    startsAt: document.getElementById('schAddTime').value,
    title: document.getElementById('schAddVisit').value.trim(),
    room: document.getElementById('schAddRoom').value.trim()
  };
  try {
    const response = await fetch('/api/visits', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      errorEl.textContent = data.error || 'Could not save the appointment.';
      return;
    }
    document.getElementById('schAddForm').reset();
    toggleSchAddForm();
    await loadProviderSchedule();
    const onBoard = body.startsOn >= BOARD_WEEK_START && body.startsOn <= addBoardDays(BOARD_WEEK_START, 4);
    showToast(onBoard
      ? 'Appointment saved. The caretaker can see it on their page.'
      : 'Appointment saved. It is outside the week shown on the board, so it appears on the caretaker page only.',
      'providerToast');
  } catch (error) {
    errorEl.textContent = "Can't reach the server. Check that it is running.";
  }
}

async function loadCaseloadNotes() {
  const list = document.getElementById('providerCareNotes');
  if (!list) return;
  try {
    const response = await fetch('/api/notes', { cache: 'no-store' });
    if (!response.ok) throw new Error('Could not load notes.');
    const { notes } = await response.json();
    list.replaceChildren(...notes.map(note => {
      const item = document.createElement('li');
      const strong = document.createElement('strong');
      strong.textContent = note.author + ': ';
      item.appendChild(strong);
      item.appendChild(document.createTextNode(note.body));
      return item;
    }));
  } catch (error) {
    // Keep whatever is already shown.
  }
}

function focusCaseloadNote() {
  const input = document.getElementById('providerCareNote');
  if (!input) return;
  input.scrollIntoView({ behavior: 'smooth', block: 'center' });
  input.focus();
}

async function addProviderCareNote() {
  const input = document.getElementById('providerCareNote');
  const note = input.value.trim();
  if (!note) {
    showToast('Enter a follow-up note first.');
    input.focus();
    return;
  }
  try {
    const response = await fetch('/api/notes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body: note })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showToast(data.error || 'Could not save the note.');
      return;
    }
    input.value = '';
    await loadCaseloadNotes();
    showToast('Note saved.');
  } catch (error) {
    showToast("Can't reach the server. The note was not saved.");
  }
}

async function sendCareMessageFromCare(event) {
  event.preventDefault();
  const patientId = document.getElementById('careMessagePatient').value;
  const bodyEl = document.getElementById('careMessageBody');
  const errorEl = document.getElementById('careMessageError');
  errorEl.textContent = '';
  try {
    const response = await fetch('/api/messages', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ patientId, body: bodyEl.value })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      errorEl.textContent = data.error || 'Could not send the message.';
      return;
    }
    bodyEl.value = '';
    showToast('Message sent to the caretaker.');
  } catch (error) {
    errorEl.textContent = "Can't reach the server. The message was not sent.";
  }
}

populateCareMessagePatients();
