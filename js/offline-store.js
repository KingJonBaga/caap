/**
 * offline-store.js — NURTURA IndexedDB outbox for BHW offline changes (design.md §5)
 *
 * Overview
 * --------
 * BHWs often work without network signal.  Every submission that would normally
 * go straight to the server is first written to an IndexedDB "outbox" with:
 *
 *   - client_id   : UUID generated on this device (crypto.randomUUID)
 *   - entity      : record type, e.g. "home_visit" | "risk_flag" | "referral"
 *   - payload     : the JSON object to send, encrypted at rest (AES-GCM)
 *   - created_at  : ISO timestamp
 *   - sync_status : "pending" | "synced" | "failed"
 *   - failed_reason : error string set when sync_status === "failed"
 *
 * Encryption
 * ----------
 * A key is derived once per sign-in from the session token using PBKDF2 / SHA-256.
 * The key lives only in memory (a module-scoped variable).  It is cleared on
 * sign-out together with the entire outbox.
 *
 * Public API
 * ----------
 *   nurturaOfflineStore.initKey(sessionToken)   — derive + cache encryption key
 *   nurturaOfflineStore.addToOutbox(entity, payload) → Promise<string>  (client_id)
 *   nurturaOfflineStore.getOutbox()             → Promise<Entry[]>  (decrypted)
 *   nurturaOfflineStore.markSynced(client_id)   → Promise<void>
 *   nurturaOfflineStore.markFailed(client_id, reason) → Promise<void>
 *   nurturaOfflineStore.clearStore()            → Promise<void>  (call on sign-out)
 *   nurturaOfflineStore.submitWithFallback(entity, payload, apiFn) → Promise<Result>
 *
 * submitWithFallback
 * ------------------
 * Attempts the network API call first (apiFn).  If the call fails because the
 * device is offline (TypeError / fetch error), the change is written to the
 * outbox instead, and {queued: true, client_id} is returned.  When the call
 * succeeds the outbox entry (if any) is marked synced automatically.
 */

(function () {
  'use strict';

  const DB_NAME    = 'nurtura-offline';
  const DB_VERSION = 1;
  const STORE_NAME = 'outbox';

  // In-memory encryption key — cleared on sign-out.
  let _encKey = null;

  // ── IndexedDB helpers ──────────────────────────────────────────────────────

  /** Open (or create) the IndexedDB database. */
  function openDB() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);

      req.onupgradeneeded = (event) => {
        const db = event.target.result;
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          const store = db.createObjectStore(STORE_NAME, { keyPath: 'client_id' });
          store.createIndex('sync_status', 'sync_status', { unique: false });
          store.createIndex('entity',      'entity',      { unique: false });
        }
      };

      req.onsuccess = (event) => resolve(event.target.result);
      req.onerror   = (event) => reject(event.target.error);
    });
  }

  /** Run a callback inside a readwrite transaction on `outbox`. */
  function withStore(mode, callback) {
    return openDB().then((db) => {
      return new Promise((resolve, reject) => {
        const tx    = db.transaction(STORE_NAME, mode);
        const store = tx.objectStore(STORE_NAME);
        let result;
        try {
          result = callback(store);
        } catch (err) {
          reject(err);
          return;
        }
        tx.oncomplete = () => resolve(result instanceof IDBRequest ? result.result : result);
        tx.onerror    = (e) => reject(e.target.error);
        tx.onabort    = (e) => reject(e.target.error);
      });
    });
  }

  // ── Web Crypto helpers ─────────────────────────────────────────────────────

  /**
   * Derive an AES-GCM-256 key from a session token using PBKDF2.
   * The derived key is kept in module memory only.
   * @param {string} sessionToken  Plain-text bearer token from caapSession.
   * @returns {Promise<void>}
   */
  async function initKey(sessionToken) {
    if (!sessionToken) {
      _encKey = null;
      return;
    }
    const enc     = new TextEncoder();
    const keyMat  = await crypto.subtle.importKey(
      'raw', enc.encode(sessionToken), { name: 'PBKDF2' }, false, ['deriveKey']
    );
    // Salt is deterministic so the same token always produces the same key within
    // a session.  It is not persisted outside of this in-memory derivation.
    const salt    = enc.encode('nurtura-offline-salt-v1');
    _encKey = await crypto.subtle.deriveKey(
      { name: 'PBKDF2', salt, iterations: 100_000, hash: 'SHA-256' },
      keyMat,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt']
    );
  }

  /**
   * Encrypt a plain JSON object.
   * @param {object} obj
   * @returns {Promise<{iv: string, ciphertext: string}>}  base-64 encoded parts
   */
  async function encryptPayload(obj) {
    if (!_encKey) throw new Error('Offline store: encryption key not initialised. Call initKey first.');
    const iv        = crypto.getRandomValues(new Uint8Array(12));
    const enc       = new TextEncoder();
    const plaintext = enc.encode(JSON.stringify(obj));
    const cipher    = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, _encKey, plaintext);
    return {
      iv:         btoa(String.fromCharCode(...iv)),
      ciphertext: btoa(String.fromCharCode(...new Uint8Array(cipher))),
    };
  }

  /**
   * Decrypt an envelope created by encryptPayload.
   * @param {{iv: string, ciphertext: string}} envelope
   * @returns {Promise<object>}
   */
  async function decryptPayload(envelope) {
    if (!_encKey) throw new Error('Offline store: encryption key not initialised.');
    const iv         = Uint8Array.from(atob(envelope.iv),         (c) => c.charCodeAt(0));
    const ciphertext = Uint8Array.from(atob(envelope.ciphertext), (c) => c.charCodeAt(0));
    const plain      = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, _encKey, ciphertext);
    return JSON.parse(new TextDecoder().decode(plain));
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  /**
   * Write a pending change to the outbox.
   * @param {string} entity   e.g. "home_visit" | "risk_flag" | "referral"
   * @param {object} payload  The data to sync later.
   * @returns {Promise<string>}  The generated client_id.
   */
  async function addToOutbox(entity, payload) {
    const client_id      = crypto.randomUUID();
    const encryptedPayload = await encryptPayload(payload);
    const entry = {
      client_id,
      entity,
      payload_enc:   encryptedPayload,  // stores encrypted envelope, not plain payload
      created_at:    new Date().toISOString(),
      sync_status:   'pending',
      failed_reason: null,
    };
    await withStore('readwrite', (store) => store.put(entry));
    return client_id;
  }

  /**
   * Return all outbox entries, with payloads decrypted.
   * @returns {Promise<Array>}
   */
  async function getOutbox() {
    const raw = await new Promise((resolve, reject) => {
      openDB().then((db) => {
        const tx    = db.transaction(STORE_NAME, 'readonly');
        const store = tx.objectStore(STORE_NAME);
        const req   = store.getAll();
        req.onsuccess = () => resolve(req.result);
        req.onerror   = (e) => reject(e.target.error);
      }).catch(reject);
    });

    // Decrypt each entry in parallel.
    return Promise.all(
      raw.map(async (entry) => {
        try {
          const payload = await decryptPayload(entry.payload_enc);
          return { ...entry, payload, payload_enc: undefined };
        } catch {
          // Key may have changed (e.g. different session).  Return without payload.
          return { ...entry, payload: null, payload_enc: undefined };
        }
      })
    );
  }

  /**
   * Mark an outbox entry as successfully synced.
   * @param {string} client_id
   * @returns {Promise<void>}
   */
  async function markSynced(client_id) {
    await openDB().then((db) => {
      return new Promise((resolve, reject) => {
        const tx    = db.transaction(STORE_NAME, 'readwrite');
        const store = tx.objectStore(STORE_NAME);
        const req   = store.get(client_id);
        req.onsuccess = () => {
          const entry = req.result;
          if (entry) {
            entry.sync_status   = 'synced';
            entry.failed_reason = null;
            store.put(entry);
          }
        };
        tx.oncomplete = () => resolve();
        tx.onerror    = (e) => reject(e.target.error);
      });
    });
  }

  /**
   * Mark an outbox entry as failed with a human-readable reason.
   * @param {string} client_id
   * @param {string} reason
   * @returns {Promise<void>}
   */
  async function markFailed(client_id, reason) {
    await openDB().then((db) => {
      return new Promise((resolve, reject) => {
        const tx    = db.transaction(STORE_NAME, 'readwrite');
        const store = tx.objectStore(STORE_NAME);
        const req   = store.get(client_id);
        req.onsuccess = () => {
          const entry = req.result;
          if (entry) {
            entry.sync_status   = 'failed';
            entry.failed_reason = reason || 'Unknown error';
            store.put(entry);
          }
        };
        tx.oncomplete = () => resolve();
        tx.onerror    = (e) => reject(e.target.error);
      });
    });
  }

  /**
   * Clear ALL outbox entries and wipe the in-memory key.
   * Called on sign-out so no data persists after the session ends (design.md §5).
   * @returns {Promise<void>}
   */
  async function clearStore() {
    _encKey = null;
    await withStore('readwrite', (store) => store.clear());
  }

  /**
   * Convenience wrapper: attempt a network call; if offline, queue in the outbox.
   *
   * Usage:
   *   const result = await nurturaOfflineStore.submitWithFallback(
   *     'home_visit',
   *     { mother_id: 1, ... },
   *     () => fetch('/api/home-visits', { method: 'POST', ... })
   *   );
   *
   *   if (result.queued) {
   *     // Stored locally — sync when back online.
   *     showToast('Saved offline. Will sync when you reconnect.');
   *   } else if (result.ok) {
   *     // Sent to server immediately.
   *   }
   *
   * @param {string}   entity    Record type ("home_visit" | "risk_flag" | "referral")
   * @param {object}   payload   Data to send / queue
   * @param {Function} apiFn     Async function that makes the fetch call; returns a Response
   * @returns {Promise<{ok: boolean, queued: boolean, client_id?: string, response?: Response, error?: Error}>}
   */
  async function submitWithFallback(entity, payload, apiFn) {
    try {
      const response = await apiFn();
      if (response.ok) {
        return { ok: true, queued: false, response };
      }
      // Server returned an error — not an offline condition; propagate.
      return { ok: false, queued: false, response };
    } catch (err) {
      // Network / fetch error — treat as offline.
      const isOfflineError = (
        err instanceof TypeError ||
        (typeof err.message === 'string' &&
          (err.message.includes('fetch') ||
           err.message.includes('network') ||
           err.message.includes('Failed to fetch') ||
           !navigator.onLine))
      );
      if (isOfflineError) {
        const client_id = await addToOutbox(entity, payload);
        return { ok: false, queued: true, client_id, error: err };
      }
      throw err;
    }
  }

  // ── Sync status and sync-now ──────────────────────────────────────────────

  /**
   * Return a summary of the current outbox state.
   * Does NOT decrypt payloads — only reads status fields.
   * @returns {Promise<{pendingCount: number, failedCount: number, failedItems: Array, lastSyncTime: string|null}>}
   */
  async function getSyncStatus() {
    const raw = await new Promise((resolve, reject) => {
      openDB().then((db) => {
        const tx    = db.transaction(STORE_NAME, 'readonly');
        const store = tx.objectStore(STORE_NAME);
        const req   = store.getAll();
        req.onsuccess = () => resolve(req.result);
        req.onerror   = (e) => reject(e.target.error);
      }).catch(reject);
    });

    let pendingCount = 0;
    let failedCount  = 0;
    const failedItems = [];

    for (const entry of raw) {
      if (entry.sync_status === 'pending') {
        pendingCount++;
      } else if (entry.sync_status === 'failed') {
        failedCount++;
        failedItems.push({
          client_id:     entry.client_id,
          entity:        entry.entity,
          failed_reason: entry.failed_reason,
          created_at:    entry.created_at,
        });
      }
    }

    // lastSyncTime is stored in localStorage so it persists across page reloads.
    const lastSyncTime = localStorage.getItem('nurtura_last_sync') || null;

    return { pendingCount, failedCount, failedItems, lastSyncTime };
  }

  /**
   * Upload all pending outbox entries to the server.
   * Uses POST /api/sync.  Marks each entry synced or failed based on the response.
   * Updates localStorage 'nurtura_last_sync' on any successful server contact.
   * After syncing, fires a custom 'nurtura:sync-complete' event on window so the
   * UI can refresh its display.
   * @returns {Promise<{synced: number, failed: number, errors: string[]}>}
   */
  async function syncNow() {
    let entries;
    try {
      entries = await getOutbox();
    } catch (err) {
      return { synced: 0, failed: 0, errors: [err.message || 'Could not read outbox'] };
    }

    const pending = entries.filter((e) => e.sync_status === 'pending' && e.payload !== null);
    if (pending.length === 0) {
      return { synced: 0, failed: 0, errors: [] };
    }

    const outbox = pending.map((e) => ({
      client_id: e.client_id,
      entity:    e.entity,
      payload:   e.payload,
    }));

    let synced = 0;
    let failed = 0;
    const errors = [];

    try {
      const token = window.caapSession ? window.caapSession.getToken() : null;
      const response = await fetch('/api/sync', {
        method:  'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: 'Bearer ' + token } : {}),
        },
        body: JSON.stringify({ outbox }),
      });

      if (!response.ok) {
        // Server returned an error — mark all as failed.
        const errText = await response.text().catch(() => 'Server error');
        for (const e of pending) {
          await markFailed(e.client_id, errText);
          failed++;
        }
        errors.push(errText);
      } else {
        const data = await response.json().catch(() => ({}));
        // Server may return per-item results: { results: [{client_id, ok, error}] }
        const resultMap = {};
        if (Array.isArray(data.results)) {
          data.results.forEach((r) => { resultMap[r.client_id] = r; });
        }

        for (const e of pending) {
          const r = resultMap[e.client_id];
          if (r && !r.ok) {
            await markFailed(e.client_id, r.error || 'Server rejected');
            failed++;
            errors.push(r.error || 'Server rejected');
          } else {
            await markSynced(e.client_id);
            synced++;
          }
        }
        // Record the sync time regardless of partial failures.
        localStorage.setItem('nurtura_last_sync', new Date().toISOString());
      }
    } catch (err) {
      // Network error — mark all pending as failed.
      for (const e of pending) {
        await markFailed(e.client_id, 'Network error: ' + (err.message || 'offline'));
        failed++;
      }
      errors.push(err.message || 'Network error');
    }

    // Notify the UI that sync has completed so it can refresh.
    try {
      window.dispatchEvent(new CustomEvent('nurtura:sync-complete'));
    } catch (_) {}

    return { synced, failed, errors };
  }

  // ── Expose as a global namespace ──────────────────────────────────────────

  window.nurturaOfflineStore = {
    initKey,
    addToOutbox,
    getOutbox,
    markSynced,
    markFailed,
    clearStore,
    submitWithFallback,
    getSyncStatus,
    syncNow,
    // Expose internal name for testing harnesses.
    _dbName: DB_NAME,
    _storeName: STORE_NAME,
  };
})();
