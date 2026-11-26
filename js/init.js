/**
 * init.js
 * App initialisation — runs after all other scripts are loaded.
 */

// ── Service worker registration (Phase 8, task 8.1) ───────────────────────────
if ('serviceWorker' in navigator) {
  navigator.serviceWorker
    .register('/sw.js')
    .then((registration) => {
      console.log('Service worker registered:', registration.scope);
    })
    .catch((error) => {
      console.error('Service worker registration failed:', error);
    });
}

// ── Existing initialisation ───────────────────────────────────────────────────
renderProviderSchedule();
setLoginRole('patient');
const landingSection = location.hash.slice(1);
if (['home', 'about', 'features', 'community', 'contact', 'help'].includes(landingSection)) {
  showLandingPage(landingSection);
}
