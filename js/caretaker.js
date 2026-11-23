/**
 * caretaker.js
 * Loads the caretaker workspace from the database and saves task and message changes.
 * Uses the same card and row styles as the care-team overview (css/provider.css).
 */

function formatCaretakerDate(isoDate) {
  const [year, month, day] = isoDate.split('-').map(Number);
  const date = new Date(year, month - 1, day);
  return date.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' });
}

function formatCaretakerTime(time24) {
  const [hours, minutes] = time24.split(':').map(Number);
  const date = new Date(2000, 0, 1, hours, minutes);
  return date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}

function formatCaretakerTimestamp(value) {
  const date = new Date(value.replace(' ', 'T'));
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
}

function initialsOf(name) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .map(word => word[0].toUpperCase())
    .slice(0, 2)
    .join('');
}

function setCaretakerText(id, text) {
  const element = document.getElementById(id);
  if (element) element.textContent = text;
}

function makeElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function renderCaretakerAppointments(appointments) {
  const container = document.getElementById('caretakerAppointments');
  container.replaceChildren();
  if (appointments.length === 0) {
    container.appendChild(makeElement('p', 'caretaker-empty', 'No appointments scheduled.'));
    return;
  }
  appointments.forEach(appointment => {
    const row = makeElement('div', 'pov-clinic-row');

    const [year, month, day] = appointment.starts_on.split('-').map(Number);
    const date = new Date(year, month - 1, day);
    const time = makeElement('span', 'pov-clinic-time', date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' }));
    time.appendChild(document.createElement('br'));
    time.appendChild(makeElement('small', '', formatCaretakerTime(appointment.starts_at)));
    row.appendChild(time);

    const avatarText = appointment.clinician ? initialsOf(appointment.clinician.replace(/^Dr\.\s*/, '')) : 'CL';
    const avatar = makeElement('span', 'pov-clinic-avatar', avatarText);
    avatar.style.background = '#c8dfd9';
    avatar.style.color = '#1a3c34';
    row.appendChild(avatar);

    const info = makeElement('div', 'pov-clinic-info');
    info.appendChild(makeElement('strong', '', appointment.title));
    const details = [appointment.clinician, appointment.location].filter(Boolean).join(' · ');
    info.appendChild(makeElement('span', '', details));
    row.appendChild(info);

    row.appendChild(makeElement('span', 'pov-appt-badge pov-appt-badge--checked', appointment.status));
    container.appendChild(row);
  });
}

function renderCaretakerTasks(tasks) {
  const list = document.getElementById('caretakerTasks');
  list.replaceChildren();
  tasks.forEach(task => {
    const item = makeElement('li', task.done ? 'is-done' : '');
    const label = makeElement('label');
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = task.done;
    checkbox.dataset.taskId = task.id;
    checkbox.addEventListener('change', () => saveCaretakerTask(task.id, checkbox));
    label.appendChild(checkbox);
    label.appendChild(makeElement('span', '', task.title));
    item.appendChild(label);
    list.appendChild(item);
  });
  updateCaretakerTaskCount(tasks);
}

function updateCaretakerTaskCount(tasks) {
  const done = tasks.filter(task => task.done).length;
  const percent = tasks.length ? Math.round((done / tasks.length) * 100) : 0;
  setCaretakerText('caretakerTasksDone', done);
  setCaretakerText('caretakerTasksTotal', tasks.length);
  const bar = document.getElementById('caretakerProgressBar');
  if (bar) bar.style.width = percent + '%';
}

function renderCaretakerMessages(messages) {
  const container = document.getElementById('caretakerMessages');
  container.replaceChildren();
  if (messages.length === 0) {
    container.appendChild(makeElement('p', 'caretaker-empty', 'No messages yet.'));
    return;
  }
  messages.forEach(message => {
    // Unread messages use the amber "action" style; read ones use the grey "routine" style.
    const item = makeElement('div', 'pov-alert-item ' + (message.read ? 'pov-alert--routine' : 'pov-alert--action'));
    const body = makeElement('div', 'pov-alert-body');
    body.appendChild(makeElement('strong', '', `${message.sender} · ${formatCaretakerTimestamp(message.sent_at)}`));
    body.appendChild(makeElement('p', '', message.body));
    if (!message.read) {
      const button = makeElement('button', 'caretaker-mark-read', 'Mark as read');
      button.type = 'button';
      button.addEventListener('click', () => markCaretakerMessageRead(message.id));
      body.appendChild(button);
    }
    item.appendChild(body);
    container.appendChild(item);
  });
}

function updateCaretakerUnread(messages) {
  const unread = messages.filter(message => !message.read);
  setCaretakerText('caretakerUnread', unread.length);
  setCaretakerText('caretakerUnreadMeta', unread.length ? `From ${unread[0].sender}` : 'All caught up');
  const badge = document.getElementById('caretakerNotifCount');
  if (badge) {
    badge.textContent = unread.length;
    badge.hidden = unread.length === 0;
  }
}

// Header nav: scrolls to a card and highlights the tab. Pass null as the button to leave the tabs alone.
function showCaretakerSection(id, button) {
  if (button) {
    document.querySelectorAll('#page-caretaker .app-nav-item').forEach(item => {
      item.classList.toggle('active', item === button);
    });
  }
  const target = id === 'top'
    ? document.querySelector('#page-caretaker .pov-greeting-bar')
    : document.getElementById(id);
  if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function loadCaretakerWorkspace() {
  try {
    const response = await fetch('/api/caretaker', { cache: 'no-store' });
    if (!response.ok) throw new Error('Could not load the workspace.');
    const workspace = await response.json();

    setCaretakerText('caretakerPatientName', workspace.patient.name);
    setCaretakerText('caretakerVisitCount', workspace.appointments.length);

    const first = workspace.appointments[0];
    if (first) {
      setCaretakerText('caretakerNextVisit', formatCaretakerDate(first.starts_on));
      setCaretakerText('caretakerNextVisitMeta', `${first.title}, ${formatCaretakerTime(first.starts_at)}`);
    } else {
      setCaretakerText('caretakerNextVisit', 'None booked');
      setCaretakerText('caretakerNextVisitMeta', '');
    }
    renderCaretakerAppointments(workspace.appointments);
    renderCaretakerTasks(workspace.tasks);
    renderCaretakerMessages(workspace.messages);
    updateCaretakerUnread(workspace.messages);
  } catch (error) {
    showToast("Couldn't load the caretaker workspace. Double-click start.bat, then open http://localhost:3000.");
  }
}

async function saveCaretakerTask(taskId, checkbox) {
  const wanted = checkbox.checked;
  try {
    const response = await fetch(`/api/caretaker/tasks/${taskId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ done: wanted })
    });
    if (!response.ok) throw new Error('Save failed');
    await loadCaretakerWorkspace();
  } catch (error) {
    checkbox.checked = !wanted; // put the box back if the save failed
    showToast("Couldn't save that task. Check that the server is running.");
  }
}

async function markCaretakerMessageRead(messageId) {
  try {
    const response = await fetch(`/api/caretaker/messages/${messageId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ read: true })
    });
    if (!response.ok) throw new Error('Save failed');
    await loadCaretakerWorkspace();
  } catch (error) {
    showToast("Couldn't update that message. Check that the server is running.");
  }
}
