"""Risk indicators and risk flags for the BHW-RHU monitoring system.

Tables
------
risk_indicators  — approved indicator definitions (starts empty; filled by qualified professionals)
risk_flags       — flags raised against a mother's record by a BHW or midwife

Design rules enforced here
--------------------------
- risk_indicators starts empty. No thresholds or rules are invented in code (task 5.1).
- risk_flags.status is constrained to ('open', 'reviewed', 'dismissed') (task 5.1).
- indicator_id is nullable: a flag may be free-text with no matching indicator (task 5.3).
- Flags are never auto-dismissed; status changes require an explicit actor (task 5.3).
"""

import json

from common import query_all, query_one, sql_time, text, transaction, utc_now

RISK_FLAG_STATUS = ("open", "reviewed", "dismissed")

# Roles allowed to create/list risk indicators (admin-only)
INDICATOR_ADMIN_ROLES = ("rhu_admin", "sysadmin")


# ── Schema ────────────────────────────────────────────────────────────────────

def setup_risk(db):
    """Create risk_indicators and risk_flags tables if they do not exist.

    Called once at database startup by db.open_database().
    risk_indicators is intentionally left empty; rows are added only through the
    admin endpoint after a qualified professional approves each indicator.
    """
    db.execute("""
        CREATE TABLE IF NOT EXISTS risk_indicators (
          id               INTEGER PRIMARY KEY AUTOINCREMENT,
          code             TEXT    NOT NULL UNIQUE,
          description      TEXT    NOT NULL DEFAULT '',
          source_reference TEXT    NOT NULL,
          rule             TEXT    NOT NULL DEFAULT '{}',
          approved_by      TEXT,
          approved_at      TEXT,
          active           INTEGER NOT NULL DEFAULT 1
                             CHECK (active IN (0, 1)),
          created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
          created_by       INTEGER REFERENCES users(id),
          updated_at       TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS risk_flags (
          id            INTEGER PRIMARY KEY AUTOINCREMENT,
          mother_id     INTEGER NOT NULL REFERENCES mothers(id),
          visit_id      INTEGER,
          indicator_id  INTEGER REFERENCES risk_indicators(id),
          observation   TEXT    NOT NULL DEFAULT '',
          raised_by     INTEGER NOT NULL REFERENCES users(id),
          raised_at     TEXT    NOT NULL DEFAULT (datetime('now')),
          status        TEXT    NOT NULL DEFAULT 'open'
                          CHECK (status IN ('open', 'reviewed', 'dismissed')),
          reviewed_by   INTEGER REFERENCES users(id),
          review_note   TEXT    NOT NULL DEFAULT '',
          created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
          created_by    INTEGER NOT NULL REFERENCES users(id),
          updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # Add visit_id column to existing databases that were created before this column existed.
    existing_cols = {row[1] for row in db.execute("PRAGMA table_info(risk_flags)").fetchall()}
    if "visit_id" not in existing_cols:
        db.execute("ALTER TABLE risk_flags ADD COLUMN visit_id INTEGER")


# ── Serialisation ─────────────────────────────────────────────────────────────

def _to_indicator(row):
    rule = row["rule"]
    try:
        rule = json.loads(rule) if isinstance(rule, str) else rule
    except (ValueError, TypeError):
        rule = {}
    return {
        "id":              row["id"],
        "code":            row["code"],
        "description":     row["description"],
        "source_reference": row["source_reference"],
        "rule":            rule,
        "approved_by":     row["approved_by"],
        "approved_at":     row["approved_at"],
        "active":          bool(row["active"]),
        "created_at":      row["created_at"],
        "created_by":      row["created_by"],
        "updated_at":      row["updated_at"],
    }


# ── Business logic ────────────────────────────────────────────────────────────

def create_indicator(db, user, data):
    """Create a new risk indicator.

    Returns {"indicator": {...}} on success.
    Returns {"status": int, "error": str} on validation failure.
    """
    code             = text(data.get("code"))
    description      = text(data.get("description") or "")
    source_reference = text(data.get("source_reference") or data.get("sourceReference") or "")
    approved_by      = text(data.get("approved_by") or data.get("approvedBy") or "")
    approved_at      = text(data.get("approved_at") or data.get("approvedAt") or "")
    active           = int(data.get("active", 1))
    rule             = data.get("rule", {})

    # Validation
    if not code:
        return {"status": 400, "error": "code is required."}
    if not source_reference:
        return {"status": 400, "error": "source_reference is required."}
    if not approved_by:
        return {"status": 400, "error": "approved_by is required."}

    # Serialise rule to JSON
    try:
        rule_json = json.dumps(rule)
    except (TypeError, ValueError):
        return {"status": 400, "error": "rule must be a JSON-serialisable object."}

    try:
        with transaction(db):
            cursor = db.execute(
                """INSERT INTO risk_indicators
                   (code, description, source_reference, rule, approved_by, approved_at, active, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    code,
                    description,
                    source_reference,
                    rule_json,
                    approved_by or None,
                    approved_at or None,
                    active,
                    user["id"],
                ),
            )
            indicator_id = cursor.lastrowid
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            return {"status": 409, "error": f"An indicator with code '{code}' already exists."}
        raise

    row = query_one(db, "SELECT * FROM risk_indicators WHERE id = ?", (indicator_id,))
    return {"indicator": _to_indicator(row)}


def list_indicators(db):
    """Return all active risk indicators."""
    rows = query_all(db, "SELECT * FROM risk_indicators WHERE active = 1 ORDER BY code")
    return [_to_indicator(r) for r in rows]


# ── Risk flags ────────────────────────────────────────────────────────────────

def _match_indicators(db, observation):
    """Return the first active indicator whose code or description matches
    the observation text (case-insensitive substring match).

    Returns the indicator row dict, or None if no match.

    Design note: the system does NOT calculate risk scores or evaluate
    threshold rules — it only does a simple keyword match on code/description.
    Rule-based matching is left for a future phase after the partner approves
    specific logic.
    """
    if not observation:
        return None
    lower = observation.lower()
    indicators = query_all(
        db,
        "SELECT * FROM risk_indicators WHERE active = 1 ORDER BY code",
    )
    for ind in indicators:
        if ind["code"].lower() in lower or (ind["description"] and ind["description"].lower() in lower):
            return ind
    return None


def record_observation(db, user, mother_id, data):
    """Record an observation for a mother, raising a risk_flag.

    Rules
    -----
    - ``observation`` (free text) is required.
    - If the observation text matches an active indicator (code or description
      substring), the flag is linked via ``indicator_id``.
    - If no indicator matches, the flag is still created with ``indicator_id=NULL``
      (free-text flag).
    - The flag is always created with ``status='open'``.  It is *never*
      auto-dismissed; only an explicit reviewer action may change its status.
    - ``visit_id`` (optional) ties the flag to a specific home/prenatal/postnatal
      visit.  When two observations are submitted for the same visit, **both rows
      are always inserted** — flags are never overwritten (design.md §5, task 8.4).

    Returns ``{"flag": {...}}`` on success or ``{"status": int, "error": str}``
    on validation failure.
    """
    observation = text(data.get("observation") or "")
    if not observation:
        return {"status": 400, "error": "observation is required."}

    if mother_id is None:
        return {"status": 400, "error": "mother_id is required."}

    # Optional visit association (task 8.4 conflict rule)
    raw_vid = data.get("visit_id") or data.get("visitId")
    visit_id = None
    if raw_vid is not None:
        try:
            visit_id = int(raw_vid)
        except (TypeError, ValueError):
            visit_id = None

    # Check mother exists
    mother = query_one(db, "SELECT id FROM mothers WHERE id = ?", (mother_id,))
    if not mother:
        return {"status": 404, "error": "Mother not found."}

    # Try to match an active indicator
    matched = _match_indicators(db, observation)
    indicator_id = matched["id"] if matched else None

    now = sql_time(utc_now())
    with transaction(db):
        cursor = db.execute(
            """INSERT INTO risk_flags
               (mother_id, visit_id, indicator_id, observation, raised_by, raised_at,
                status, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
            (mother_id, visit_id, indicator_id, observation, user["id"], now, user["id"], now, now),
        )
        flag_id = cursor.lastrowid

    row = query_one(db, "SELECT * FROM risk_flags WHERE id = ?", (flag_id,))
    return {"flag": _to_flag(row)}


def list_flags(db, mother_id=None, status_filter=None):
    """Return risk flags, optionally filtered by mother and/or status.

    For the RHU queue (task 5.4), callers pass ``status_filter='open'``
    to get only open flags.
    """
    sql = "SELECT * FROM risk_flags WHERE 1=1"
    params = []
    if mother_id is not None:
        sql += " AND mother_id = ?"
        params.append(mother_id)
    if status_filter:
        sql += " AND status = ?"
        params.append(status_filter)
    sql += " ORDER BY raised_at DESC"
    rows = query_all(db, sql, params)
    return [_to_flag(r) for r in rows]


# ── RHU queue (task 5.4) ──────────────────────────────────────────────────────

QUEUE_REVIEW_LABEL = "Requires review by a qualified health professional."

# Roles allowed to view the RHU open-flags queue
FLAG_QUEUE_ROLES = ("rhu_admin", "midwife", "physician")


def _to_flag(row):
    """Serialise a risk_flags row for the queue response."""
    return {
        "id":           row["id"],
        "mother_id":    row["mother_id"],
        "visit_id":     row["visit_id"],
        "indicator_id": row["indicator_id"],
        "observation":  row["observation"],
        "raised_by":    row["raised_by"],
        "raised_at":    row["raised_at"],
        "status":       row["status"],
        "reviewed_by":  row["reviewed_by"],
        "review_note":  row["review_note"],
        "created_at":   row["created_at"],
        "updated_at":   row["updated_at"],
        # Required label – always present on every queue item (task 5.4)
        "review_label": QUEUE_REVIEW_LABEL,
    }


def list_open_flags(db):
    """Return every open risk flag, oldest first, each carrying the review label.

    Ordered by raised_at ascending so the oldest (most urgent) flag appears first.
    """
    rows = query_all(
        db,
        "SELECT * FROM risk_flags WHERE status = 'open' ORDER BY raised_at ASC, id ASC",
    )
    return [_to_flag(r) for r in rows]
