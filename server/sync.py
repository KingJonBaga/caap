"""Offline outbox sync endpoint (design.md §5, task 8.3).

The BHW device accumulates changes while offline.  When connectivity returns,
the client POSTs ``{"outbox": [...]}`` to ``POST /api/sync``.  Each item is:

    {
        "client_id": "<UUID generated on device>",
        "entity":    "home_visit" | "risk_flag" | "referral",
        "payload":   { ... entity-specific fields ... }
    }

Idempotency
-----------
``sync_outbox`` acts as a deduplication table.  If a ``client_id`` already
exists in that table the item is skipped and ``{"status": "duplicate"}`` is
returned for it.  Sending the same outbox twice is therefore safe.

Entity dispatch
---------------
Supported entities and the existing module functions they delegate to:

    home_visit  → visit_records.create_home_visit(db, user, mother_id, payload)
    risk_flag   → risk.record_observation(db, user, mother_id, payload)
    referral    → referrals.create_referral(db, user, payload)

``mother_id`` is read from ``payload["mother_id"]`` or ``payload["motherId"]``
(the front-end typically uses camelCase).

Unknown entities are accepted and stored in ``sync_outbox`` but no secondary
write is performed; they return ``{"status": "processed", "note": "unrecognised entity stored"}``
so a protocol upgrade on the server cannot brick old clients.

Conflict rule (design.md §5)
-----------------------------
Clinical flags are NEVER silently overwritten: each call to record_observation
always creates a new flag row, so two flags for the same visit are both kept.
For other entities the existing module validation applies (e.g. duplicate
home_visit rows are allowed because BHWs may legitimately plan multiple visits).

Failed items (validation error from the module) return
``{"status": "error", "error": "<message>"}`` and are NOT added to sync_outbox
so the client can retry after correcting the data.
"""

import hashlib
import json

from common import query_one, sql_time, transaction, utc_now
import visit_records as vr
import risk as risk_module
import referrals as referrals_module

# Roles allowed to upload the offline outbox.
SYNC_ROLES = ("bhw", "midwife")


# ── Schema ────────────────────────────────────────────────────────────────────

def setup_sync(db):
    """Create sync_outbox if it does not exist. Called by db.open_database()."""
    db.execute(
        """CREATE TABLE IF NOT EXISTS sync_outbox (
               client_id    TEXT PRIMARY KEY,
               entity       TEXT NOT NULL,
               payload_hash TEXT NOT NULL,
               received_at  TEXT NOT NULL
           )"""
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _payload_hash(payload):
    """SHA-256 hex digest of the canonical JSON encoding of ``payload``."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _record_in_outbox(db, client_id, entity, payload):
    """Insert a row into sync_outbox.  Caller must hold a transaction."""
    db.execute(
        """INSERT INTO sync_outbox (client_id, entity, payload_hash, received_at)
           VALUES (?, ?, ?, ?)""",
        (client_id, entity, _payload_hash(payload), sql_time(utc_now())),
    )


# ── Entity dispatch ───────────────────────────────────────────────────────────

def _mother_id_from(payload):
    """Read mother_id from camelCase or snake_case payload key."""
    raw = payload.get("mother_id") or payload.get("motherId")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _dispatch(db, user, entity, payload):
    """Dispatch a single entity write to the appropriate module.

    Returns a dict with at least ``"ok": bool`` and optionally ``"error": str``.
    """
    if entity == "home_visit":
        mother_id = _mother_id_from(payload)
        if mother_id is None:
            return {"ok": False, "error": "home_visit: mother_id is required."}
        result = vr.create_home_visit(db, user, mother_id, payload)
        if "visit" in result:
            return {"ok": True}
        return {"ok": False, "error": result.get("error", "Failed to create home visit.")}

    if entity == "risk_flag":
        mother_id = _mother_id_from(payload)
        if mother_id is None:
            return {"ok": False, "error": "risk_flag: mother_id is required."}
        result = risk_module.record_observation(db, user, mother_id, payload)
        if "flag" in result:
            return {"ok": True}
        return {"ok": False, "error": result.get("error", "Failed to record observation.")}

    if entity == "referral":
        result = referrals_module.create_referral(db, user, payload)
        if "referral" in result:
            return {"ok": True}
        return {"ok": False, "error": result.get("error", "Failed to create referral.")}

    # Unknown entity — store in outbox but do no secondary write.
    return {"ok": True, "unknown": True}


# ── Main processing function ──────────────────────────────────────────────────

def process_outbox(db, user, outbox):
    """Process an ordered list of outbox items idempotently.

    Parameters
    ----------
    db      : open SQLite connection
    user    : authenticated user dict (must have ``id`` and ``role``)
    outbox  : list of dicts, each with ``client_id``, ``entity``, ``payload``

    Returns
    -------
    list of ``{"client_id": ..., "status": "processed"|"duplicate"|"error",
               "error": <str, only on error>}``
    """
    results = []
    for item in outbox:
        client_id = item.get("client_id") or ""
        entity    = item.get("entity")    or ""
        payload   = item.get("payload")   or {}

        if not client_id:
            results.append({"client_id": client_id, "status": "error",
                             "error": "client_id is required."})
            continue

        if not entity:
            results.append({"client_id": client_id, "status": "error",
                             "error": "entity is required."})
            continue

        # ── Idempotency check ─────────────────────────────────────────────────
        existing = query_one(
            db, "SELECT client_id FROM sync_outbox WHERE client_id = ?", (client_id,)
        )
        if existing:
            results.append({"client_id": client_id, "status": "duplicate"})
            continue

        # ── Dispatch to entity module ─────────────────────────────────────────
        dispatch_result = _dispatch(db, user, entity, payload)
        if not dispatch_result["ok"]:
            # Validation / business-logic error — do NOT record in outbox.
            results.append({
                "client_id": client_id,
                "status":    "error",
                "error":     dispatch_result["error"],
            })
            continue

        # ── Record idempotency key ────────────────────────────────────────────
        with transaction(db):
            _record_in_outbox(db, client_id, entity, payload)

        entry = {"client_id": client_id, "status": "processed"}
        if dispatch_result.get("unknown"):
            entry["note"] = "unrecognised entity stored"
        results.append(entry)

    return results
