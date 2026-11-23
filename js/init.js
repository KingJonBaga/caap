/**
 * init.js
 * App initialisation — runs after all other scripts are loaded.
 */
renderProviderSchedule();
setLoginRole('patient');
const landingSection = location.hash.slice(1);
if (['home', 'about', 'features', 'community', 'contact', 'help'].includes(landingSection)) {
  showLandingPage(landingSection);
}
