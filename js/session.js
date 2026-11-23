/**
 * session.js
 * Keeps the signed-in token for this browser tab and sends it with every API request.
 * The token is stored in sessionStorage, so it's removed when the tab is closed.
 */
(function () {
  const TOKEN_KEY = 'nurturaToken';
  const SERVER_ORIGIN = 'http://localhost:3000';
  const PUBLIC_API = ['/api/login', '/api/register', '/api/password-reset', '/api/password-reset/confirm'];
  const originalFetch = window.fetch.bind(window);

  function getToken() {
    try {
      return sessionStorage.getItem(TOKEN_KEY) || '';
    } catch (error) {
      return '';
    }
  }

  function setToken(token) {
    try {
      if (token) sessionStorage.setItem(TOKEN_KEY, token);
      else sessionStorage.removeItem(TOKEN_KEY);
    } catch (error) {
      // Storage blocked: the user simply has to sign in again.
    }
  }

  window.caapSession = { getToken, setToken };

  window.fetch = function (input, init = {}) {
    // Opened straight from a file, /api/... would point at the file system, so send it to the server.
    if (window.location.protocol === 'file:' && typeof input === 'string' && input.startsWith('/api/')) {
      input = SERVER_ORIGIN + input;
    }
    const url = typeof input === 'string' ? input : input.url;
    const path = new URL(url, window.location.href).pathname;
    const token = getToken();
    if (path.startsWith('/api/') && token && !PUBLIC_API.includes(path)) {
      const headers = new Headers(init.headers || {});
      headers.set('Authorization', 'Bearer ' + token);
      init = { ...init, headers };
    }
    return originalFetch(input, init);
  };
})();
