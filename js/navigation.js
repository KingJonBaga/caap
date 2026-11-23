/**
 * navigation.js
 * Page routing, tab switching, section scrolling, and landing navigation.
 */

// Glides the page to a height. Done in code so the movement is smooth in every browser,
// and it is instant for people who have asked their computer to reduce motion.
function glideTo(top) {
  const start = window.scrollY;
  const distance = top - start;
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduce || Math.abs(distance) < 2) {
    window.scrollTo(0, top);
    return;
  }
  const duration = Math.min(1000, 450 + Math.abs(distance) * 0.2);
  const startTime = performance.now();
  function step(now) {
    const t = Math.min(1, (now - startTime) / duration);
    const eased = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; // ease in and out
    window.scrollTo(0, start + distance * eased);
    if (t < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function scrollToSection(name) {
  const target = document.getElementById(name);
  if (!target) return;
  if (name === 'home') {
    glideTo(0);
  } else {
    const navHeight = document.querySelector('.ln-nav').offsetHeight;
    const top = target.getBoundingClientRect().top + window.scrollY - navHeight;
    glideTo(top);
  }
  document.querySelectorAll('.ln-nav-links a').forEach(link => {
    link.removeAttribute('aria-current');
    if (link.getAttribute('href') === '#' + name) link.setAttribute('aria-current', 'page');
  });
  history.replaceState(null, '', '#' + name);
}

function showLandingPage(name) {
  scrollToSection(name);
}

function selectLandingRole(role) {
  if (role !== 'patient') {
    clearPatientDemoCredentials();
  }
  setLoginRole(role);
  scrollToSection('home');
}

function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  document.documentElement.classList.toggle('admin-dashboard-active', name === 'admin');
  if (name === 'caretaker' && typeof loadCaretakerWorkspace === 'function') {
    loadCaretakerWorkspace();
  }
  if (name === 'provider' && typeof loadProviderSchedule === 'function') {
    loadProviderSchedule();
    loadCaseloadNotes();
  }
  if (name === 'profile') {
    const profileMain = document.querySelector('#page-profile .profile-main');
    profileMain.querySelectorAll('.profile-tab').forEach(tab => tab.classList.remove('active'));
    profileMain.querySelectorAll('.tab-panel').forEach(panel => panel.classList.remove('active'));
    profileMain.querySelector('.profile-tab[onclick*="profile-info"]').classList.add('active');
    profileMain.querySelector('#profile-info').classList.add('active');
  }
  if (name === 'records') {
    showRecordTab('visits', document.querySelector('#page-records .records-tab'));
  }
  if (name === 'appointments') {
    showAppointmentTab('upcoming', document.querySelector('#page-appointments .appointment-tab'));
  }
  if (name === 'postpartum') {
    updatePostpartumProgress();
  }
  window.scrollTo(0, 0);
}

function showRecordTab(name, button) {
  document.querySelectorAll('#page-records .records-tab').forEach(tab => {
    const isActive = tab === button;
    tab.classList.toggle('active', isActive);
    tab.setAttribute('aria-selected', String(isActive));
  });
  document.querySelectorAll('#page-records .patient-records-content > .records-panel').forEach(panel => {
    const isActive = panel.id === 'record-' + name;
    panel.classList.toggle('active', isActive);
    panel.hidden = !isActive;
  });
}

function showAppointmentTab(name, button) {
  document.querySelectorAll('#page-appointments .appointment-tab').forEach(tab => {
    const isActive = tab === button;
    tab.classList.toggle('active', isActive);
    tab.setAttribute('aria-selected', String(isActive));
  });
  document.querySelectorAll('#page-appointments .appointment-panel').forEach(panel => {
    const isActive = panel.id === 'appointments-' + name;
    panel.classList.toggle('active', isActive);
    panel.hidden = !isActive;
  });
}

function updatePostpartumProgress() {
  const checkboxes = document.querySelectorAll('#page-postpartum .postpartum-task-list input[type="checkbox"]');
  const completed = [...checkboxes].filter(checkbox => checkbox.checked).length;
  document.getElementById('postpartumCompletedCount').textContent = completed;
  document.getElementById('postpartumProgressFill').style.width = (completed / checkboxes.length * 100) + '%';
  document.querySelector('.postpartum-progress-track').setAttribute('aria-valuenow', completed);
}

function showProfileTab(panelId) {
  showPage('profile');
  const tab = document.querySelector('.profile-tab[onclick*="' + panelId + '"]');
  if (!tab) {
    throw new Error('Profile tab not found: ' + panelId);
  }
  switchTab(tab, panelId);
}

function showPatientAppointments() {
  showPage('appointments');
}

function switchTab(btn, panelId) {
  const root = btn.closest('.profile-main');
  root.querySelectorAll('.profile-tab').forEach(t => t.classList.remove('active'));
  root.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById(panelId).classList.add('active');
}

function handleContactMessage(event) {
  event.preventDefault();
  document.getElementById('contactFormStatus').textContent =
    'Thank you. This demo form does not send or save messages. Please contact your clinic directly for care-related questions.';
}

function completeAppointment(button) {
  button.textContent = 'Completed';
  button.disabled = true;
  button.classList.add('is-complete');
  button.closest('.provider-appointment').querySelector('.provider-time').textContent += ' · Done';
  showToast('Sample appointment marked complete temporarily; the change is not saved.');
}

// Opens the page named after # in the address, e.g. index.html#caretaker.
// The old html/ files forward here, so each link opens the matching page.
window.addEventListener('load', () => {
  const name = decodeURIComponent(window.location.hash.replace('#', ''));
  if (name && document.getElementById('page-' + name)) showPage(name);
});
