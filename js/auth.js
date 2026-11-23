/**
 * auth.js
 * Login form handling, role detection, identity application, and password toggle.
 */

const demoEmailRoles = {
  'maria.santos@example.com': 'patient',
  'nina.flores@example.com': 'patient',
  'a.rahman@mhms.example': 'provider',
  'e.reyes@mhms.example': 'provider',
  'l.park@mhms.example': 'nurse',
  'd.cruz@mhms.example': 'caretaker',
  'admin@example.com': 'admin'
};

function setLoginRole(role) {
  document.getElementById('loginRole').value = role;
  const badge = document.getElementById('loginRoleBadge');
  const roleDetails = {
    patient: { icon: 'fa-regular fa-heart', label: 'PATIENT SPACE' },
    provider: { icon: 'fa-solid fa-user-doctor', label: 'CARE TEAM SPACE' },
    nurse: { icon: 'fa-solid fa-user-nurse', label: 'NURSE SPACE' },
    caretaker: { icon: 'fa-solid fa-hand-holding-heart', label: 'CARETAKER SPACE' },
    admin: { icon: 'fa-solid fa-user-shield', label: 'ADMINISTRATOR SPACE' }
  };
  const selected = roleDetails[role];
  badge.innerHTML = '<i class="' + selected.icon + '"></i> ' + selected.label;
  document.querySelectorAll('[data-login-role]').forEach(link => {
    const isSelected = link.dataset.loginRole === role;
    link.classList.toggle('selected', isSelected);
  });
  const patientLoginLink = document.querySelector('.patient-login-link');
  if (patientLoginLink) patientLoginLink.hidden = role === 'patient';
}

function syncLoginRoleFromEmail() {
  const role = demoEmailRoles[document.getElementById('email').value.trim().toLowerCase()];
  if (role) {
    if (role !== 'patient') {
      clearPatientDemoCredentials();
    }
    setLoginRole(role);
  }
}

function clearPatientDemoCredentials() {
  const email = document.getElementById('email');
  const password = document.getElementById('password');
  if (email.value.trim().toLowerCase() === 'maria.santos@example.com') {
    email.value = '';
    password.value = '';
  }
  email.classList.remove('error');
  password.classList.remove('error');
  document.getElementById('emailError').textContent = '';
  document.getElementById('pwError').textContent = '';
}

function handleLogin(e) {
  e.preventDefault();
  const email = document.getElementById('email');
  const pw = document.getElementById('password');
  const eErr = document.getElementById('emailError');
  const pErr = document.getElementById('pwError');
  let ok = true;

  eErr.textContent = ''; pw && (pErr.textContent = '');
  email.classList.remove('error'); pw.classList.remove('error');

  if (!email.value.trim()) {
    eErr.textContent = 'Please enter your email address.';
    email.classList.add('error'); ok = false;
  } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.value.trim())) {
    eErr.textContent = 'Please enter a valid email address.';
    email.classList.add('error'); ok = false;
  }
  if (!pw.value) {
    pErr.textContent = 'Please enter your password.';
    pw.classList.add('error'); ok = false;
  } else if (pw.value.length < 6) {
    pErr.textContent = 'Password must be at least 6 characters.';
    pw.classList.add('error'); ok = false;
  }
  if (!ok) return;

  const btn = document.getElementById('submitBtn');
  const resetButton = () => {
    btn.innerHTML = "Let's get you signed in <i class='fa-solid fa-arrow-right'></i>";
    btn.disabled = false;
  };
  btn.innerHTML = 'Signing you in… <i class="fa-solid fa-spinner fa-spin"></i>';
  btn.disabled = true;

  signInWithDatabase(email.value.trim(), pw.value).then(result => {
    resetButton();
    if (result.error) {
      eErr.textContent = result.message;
      return;
    }
    if (!result.user) {
      pErr.textContent = result.message;
      pw.classList.add('error');
      return;
    }
    // The role comes from the database, not from the role chooser.
    const loginRole = result.user.role;
    setLoginRole(loginRole);
    const destination = { patient: 'dashboard', provider: 'provider', nurse: 'provider', caretaker: 'caretaker', admin: 'admin' };
    const page = destination[loginRole];
    if (!page) {
      eErr.textContent = 'No workspace is configured for this account.';
      return;
    }
    applyStaffIdentity(loginRole);
    showPage(page);
  });
}

// Ends the session on the server and returns to the sign-in page.
async function signOut() {
  try {
    if (window.caapSession.getToken()) await fetch('/api/session', { method: 'DELETE' });
  } catch (error) {
    // Server unreachable: still clear the token in this tab.
  }
  window.caapSession.setToken('');
  // Reload so no patient or caretaker data stays in memory after signing out.
  window.location.reload();
}

// Step 1 of password reset: asks the server for a code (it is written to the server terminal).
async function handleResetRequest(e) {
  e.preventDefault();
  const email = document.getElementById('resetEmail');
  const error = document.getElementById('resetEmailError');
  error.textContent = '';
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.value.trim())) {
    error.textContent = 'Please enter a valid email address.';
    return;
  }
  try {
    const response = await fetch('/api/password-reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email.value.trim() })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Could not send a code.');
    document.getElementById('resetStepCode').hidden = false;
    document.getElementById('resetCodeInfo').textContent = data.message;
    document.getElementById('resetCode').focus();
  } catch (err) {
    error.textContent = err.message || "Can't reach the server.";
  }
}

// Step 2 of password reset: the code plus a new password.
async function handleResetConfirm(e) {
  e.preventDefault();
  const code = document.getElementById('resetCode');
  const password = document.getElementById('resetNewPassword');
  const confirm = document.getElementById('resetConfirmPassword');
  const error = document.getElementById('resetConfirmError');
  error.textContent = '';
  if (password.value.length < 8) {
    error.textContent = 'Password must be at least 8 characters.';
    return;
  }
  if (password.value !== confirm.value) {
    error.textContent = 'The passwords do not match.';
    return;
  }
  try {
    const response = await fetch('/api/password-reset/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: code.value, password: password.value })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Could not reset your password.');
    document.getElementById('resetForm').reset();
    document.getElementById('resetStepCode').hidden = true;
    document.getElementById('resetCodeInfo').textContent = '';
    showPage('login');
    showToast('Password changed. Sign in with your new password.');
  } catch (err) {
    error.textContent = err.message || "Can't reach the server.";
  }
}

// Creates a patient account in the database, then sends the user to the sign-in form.
async function handleRegister(e) {
  e.preventDefault();
  const fields = {
    name: document.getElementById('registerName'),
    email: document.getElementById('registerEmail'),
    password: document.getElementById('registerPassword'),
    confirm: document.getElementById('registerConfirm')
  };
  Object.keys(fields).forEach(key => {
    fields[key].classList.remove('error');
    document.getElementById('register' + key[0].toUpperCase() + key.slice(1) + 'Error').textContent = '';
  });

  let ok = true;
  const fail = (key, message) => {
    document.getElementById('register' + key[0].toUpperCase() + key.slice(1) + 'Error').textContent = message;
    fields[key].classList.add('error');
    ok = false;
  };
  if (!fields.name.value.trim()) fail('name', 'Please enter your full name.');
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(fields.email.value.trim())) fail('email', 'Please enter a valid email address.');
  if (fields.password.value.length < 8) fail('password', 'Password must be at least 8 characters.');
  if (fields.confirm.value !== fields.password.value) fail('confirm', 'The passwords do not match.');
  if (!ok) return;

  const button = document.getElementById('registerBtn');
  button.disabled = true;
  try {
    const response = await fetch('/api/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: fields.name.value,
        email: fields.email.value,
        password: fields.password.value
      })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      fail(response.status === 409 ? 'email' : 'name', data.error || 'Could not create your account.');
      return;
    }
    document.getElementById('email').value = data.user.email;
    document.getElementById('password').value = '';
    document.getElementById('registerForm').reset();
    showPage('login');
    showToast('Account created. Sign in with your new password.');
  } catch (error) {
    fail('name', "Can't reach the server. Double-click start.bat, then open http://localhost:3000.");
  } finally {
    button.disabled = false;
  }
}

// Checks the email and password against the database (server/users.js).
async function signInWithDatabase(email, password) {
  try {
    const response = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password })
    });
    const data = await response.json().catch(() => ({}));
    if (response.ok) {
      window.caapSession.setToken(data.token);
      return { user: data.user };
    }
    return { message: data.error || 'Sign-in failed. Please try again.' };
  } catch (error) {
    return { error: true, message: "Can't reach the server. Double-click start.bat, then open http://localhost:3000." };
  }
}

function applyStaffIdentity(role) {
  const identities = {
    provider: { initials: 'AO', name: 'Dr. Adaeze Okafor', meta: 'Obstetrician · Care team lead', badge: 'CARE TEAM', crumb: 'Care Provider Workspace', greeting: 'Good morning, Okafor.' },
    nurse: { initials: 'NL', name: 'Nurse Lena Park', meta: 'Nurse · Care team', badge: 'CARE TEAM', crumb: 'Nurse Workspace', greeting: 'Good morning, Lena.' }
  };
  const id = identities[role];
  if (!id) return;
  document.getElementById('providerAvatar').textContent = id.initials;
  document.getElementById('providerName').textContent = id.name;
  document.getElementById('providerMeta').textContent = id.meta;
  document.getElementById('providerSpaceBadge').textContent = id.badge;
  document.getElementById('providerGreeting').textContent = id.greeting;
}

function togglePw() {
  const pw = document.getElementById('password');
  const icon = document.getElementById('pwIcon');
  const show = pw.type === 'password';
  pw.type = show ? 'text' : 'password';
  icon.className = show ? 'fa-regular fa-eye-slash' : 'fa-regular fa-eye';
}
