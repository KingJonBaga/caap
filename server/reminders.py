"""Reminders for the BHW-RHU monitoring system.

Table
-----
reminders — one row per scheduled reminder; records channel, message, timing, and send result.

Design rules enforced here
--------------------------
- channel is constrained to ('in_app', 'sms').  The 'sms' adapter is a stub until the
  implementation partner confirms a gateway (design.md section 12, question 5).
- Either mother_id or user_id (or both) must be provided; a reminder with neither is
  rejected with 400.
- message must be non-empty.
- send_at must be provided (ISO-8601 datetime string).
- send_in_app marks the reminder as sent immediately.  No external call is made.
- Failed sends record their reason in failed_reason and are shown to the sender.
"""

from common import query_all, query_one, text, transaction


# ── SMS Gateway Interface ─────────────────────────────────────────────────────

class SmsGatewayNotConfigured(Exception):
    """Raised when no SMS gateway has been configured."""
    pass


class SmsGateway:
    """Base class for SMS gateway adapters.

    Subclasses must implement send(to, message) which returns None on success
    or raises an exception with a descriptive message on failure.
    """

    def send(self, to, message):
        """Send an SMS message.

        Parameters
        ----------
        to      : str — recipient phone number
        message : str — message body

        Returns None on success.
        Raises an exception with a descriptive message on failure.
        """
        raise NotImplementedError("Subclasses must implement send()")


class NullSmsGateway(SmsGateway):
    """Default SMS gateway that always fails.

    Used when no real gateway has been configured by the implementation partner.
    """

    def send(self, to, message):
        raise SmsGatewayNotConfigured("SMS gateway not configured.")

VALID_CHANNELS = ("in_app", "sms")

# Roles that may create or view reminders.
REMINDER_ROLES = ("bhw", "midwife", "physician", "rhu_admin")

# Global SMS gateway instance. Defaults to NullSmsGateway; can be replaced
# when the implementation partner confirms a gateway.
_sms_gateway = None


def set_sms_gateway(gateway):
    """Replace the SMS gateway with a custom implementation.

    Parameters
    ----------
    gateway : SmsGateway or callable with send(to, message) signature
    """
    global _sms_gateway
    _sms_gateway = gateway


def get_sms_gateway():
    """Return the current SMS gateway instance.

    Returns NullSmsGateway if none has been configured.
    """
    if _sms_gateway is None:
        return NullSmsGateway()
    return _sms_gateway


# ── Schema ────────────────────────────────────────────────────────────────────

def setup_reminders(db):
    """Create the reminders table if it does not exist.

    Called once at database startup by db.open_database().
    """
    db.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
          id             INTEGER PRIMARY KEY AUTOINCREMENT,
          mother_id      INTEGER REFERENCES mothers(id),
          user_id        INTEGER REFERENCES users(id),
          channel        TEXT    NOT NULL
                           CHECK (channel IN ('in_app', 'sms')),
          message        TEXT    NOT NULL,
          send_at        TEXT    NOT NULL,
          sent_at        TEXT,
          failed_reason  TEXT,
          created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)


# ── Serialisation ─────────────────────────────────────────────────────────────

def _to_reminder(row):
    return {
        "id":           row["id"],
        "mother_id":    row["mother_id"],
        "user_id":      row["user_id"],
        "channel":      row["channel"],
        "message":      row["message"],
        "send_at":      row["send_at"],
        "sent_at":      row["sent_at"],
        "failed_reason": row["failed_reason"],
        "created_at":   row["created_at"],
    }


# ── Business logic ────────────────────────────────────────────────────────────

def create_reminder(db, data):
    """Create a new reminder row.

    Required fields: message, send_at, channel.
    At least one of mother_id or user_id must be provided.

    Returns {"reminder": {...}} on success.
    Returns {"status": int, "error": str} on validation failure.
    """
    mother_id = data.get("mother_id") or data.get("motherId")
    user_id   = data.get("user_id")   or data.get("userId")
    channel   = text(data.get("channel") or "")
    message   = text(data.get("message") or "")
    send_at   = text(data.get("send_at") or data.get("sendAt") or "")

    # ── Validation ────────────────────────────────────────
    if not message:
        return {"status": 400, "error": "message is required."}

    if not send_at:
        return {"status": 400, "error": "send_at is required."}

    if not channel:
        return {"status": 400, "error": "channel is required."}

    if channel not in VALID_CHANNELS:
        return {
            "status": 400,
            "error": f"channel must be one of: {', '.join(VALID_CHANNELS)}.",
        }

    if mother_id is None and user_id is None:
        return {"status": 400, "error": "Either mother_id or user_id must be provided."}

    # Coerce ids to int where possible
    if mother_id is not None:
        try:
            mother_id = int(mother_id)
        except (TypeError, ValueError):
            return {"status": 400, "error": "mother_id must be an integer."}

    if user_id is not None:
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            return {"status": 400, "error": "user_id must be an integer."}

    # ── Insert ────────────────────────────────────────────
    with transaction(db):
        cursor = db.execute(
            """INSERT INTO reminders (mother_id, user_id, channel, message, send_at)
               VALUES (?, ?, ?, ?, ?)""",
            (mother_id, user_id, channel, message, send_at),
        )
        reminder_id = cursor.lastrowid

    row = query_one(db, "SELECT * FROM reminders WHERE id = ?", (reminder_id,))
    return {"reminder": _to_reminder(row)}


def list_reminders(db, user_id=None, mother_id=None, channel=None):
    """Return reminders, optionally filtered.

    Parameters
    ----------
    user_id   : int|None — filter by reminders targeted at this user
    mother_id : int|None — filter by reminders for this mother
    channel   : str|None — filter by channel value
    """
    where  = []
    values = []

    if user_id is not None:
        where.append("user_id = ?")
        values.append(user_id)
    if mother_id is not None:
        where.append("mother_id = ?")
        values.append(mother_id)
    if channel is not None:
        where.append("channel = ?")
        values.append(channel)

    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = query_all(db, f"SELECT * FROM reminders {clause} ORDER BY send_at", values)
    return [_to_reminder(r) for r in rows]


def mark_sent(db, reminder_id):
    """Set sent_at = datetime('now') on a reminder.

    Returns the updated reminder dict, or None if not found.
    """
    db.execute(
        "UPDATE reminders SET sent_at = datetime('now') WHERE id = ?",
        (reminder_id,),
    )
    row = query_one(db, "SELECT * FROM reminders WHERE id = ?", (reminder_id,))
    return _to_reminder(row) if row else None


def mark_failed(db, reminder_id, reason):
    """Record a failure reason on a reminder.

    Returns the updated reminder dict, or None if not found.
    """
    db.execute(
        "UPDATE reminders SET failed_reason = ? WHERE id = ?",
        (text(reason) or "unknown error", reminder_id),
    )
    row = query_one(db, "SELECT * FROM reminders WHERE id = ?", (reminder_id,))
    return _to_reminder(row) if row else None


def send_in_app(db, reminder_id):
    """Send a reminder via the in_app channel.

    For in_app delivery there is no external call: the reminder is considered
    sent as soon as it is delivered to the in-app queue.  This function marks
    it sent immediately.

    Returns {"reminder": {...}} on success.
    Returns {"status": 404, "error": ...} if the reminder does not exist.
    Returns {"status": 400, "error": ...} if the reminder channel is not 'in_app'.
    """
    row = query_one(db, "SELECT * FROM reminders WHERE id = ?", (reminder_id,))
    if not row:
        return {"status": 404, "error": "Reminder not found."}

    if row["channel"] != "in_app":
        return {
            "status": 400,
            "error": f"send_in_app can only be used for in_app reminders (channel is '{row['channel']}').",
        }

    reminder = mark_sent(db, reminder_id)
    return {"reminder": reminder}


def send_sms(db, reminder_id, gateway=None):
    """Send a reminder via the SMS channel.

    Uses the SMS gateway returned by get_sms_gateway() unless a custom gateway
    is passed.  Since no gateway is confirmed by the implementation partner yet,
    the default NullSmsGateway always fails and records the failure.

    Parameters
    ----------
    db          : database connection
    reminder_id : int
    gateway     : SmsGateway|None — if provided, used instead of the global gateway

    Returns {"reminder": {...}} on success (gateway called without raising).
    Returns {"status": 404, "error": ...} if the reminder does not exist.
    Returns {"status": 400, "error": ...} if the channel is not 'sms'.
    Returns {"status": 503, "error": "SMS gateway not configured.", "reminder": {...}}
            if the gateway raises any exception (including SmsGatewayNotConfigured).
    """
    row = query_one(db, "SELECT * FROM reminders WHERE id = ?", (reminder_id,))
    if not row:
        return {"status": 404, "error": "Reminder not found."}

    if row["channel"] != "sms":
        return {
            "status": 400,
            "error": f"send_sms can only be used for sms reminders (channel is '{row['channel']}').",
        }

    gw = gateway if gateway is not None else get_sms_gateway()

    # The recipient phone number is stored in the message recipient field.
    # For now we pass an empty string as `to` because reminders do not store
    # a phone number directly; the caller should supply a gateway that knows
    # how to look up the number from mother_id / user_id.
    to = ""
    try:
        gw.send(to, row["message"])
    except Exception as exc:
        reason = str(exc) if str(exc) else "SMS gateway not configured."
        reminder = mark_failed(db, reminder_id, reason)
        return {
            "status": 503,
            "error": "SMS gateway not configured.",
            "reminder": reminder,
        }

    reminder = mark_sent(db, reminder_id)
    return {"reminder": reminder}
