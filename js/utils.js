/**
 * utils.js
 * Shared utility functions used across all pages.
 */

function showToast(message, toastId = 'appToast') {
  const toast = document.getElementById(toastId);
  if (!toast) {
    throw new Error('Notification area not found: ' + toastId);
  }
  toast.textContent = message;
  toast.classList.add('visible');
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => toast.classList.remove('visible'), 2600);
}

function showAdminToast(message) {
  const toast = document.getElementById('adminToast');
  toast.textContent = message;
  toast.classList.add('visible');
  window.clearTimeout(showAdminToast.timeout);
  showAdminToast.timeout = window.setTimeout(() => toast.classList.remove('visible'), 2600);
}
