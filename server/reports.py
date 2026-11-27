"""Monthly aggregated report for the RHU admin workspace (task 9.2).

Returns count-only data for a given calendar month. The response NEVER
contains names, contact details, addresses, dates of birth, phone numbers,
e-mail addresses, or any other personally identifying information.

Counts are integers, except that any count from 1 to MIN_GROUP_SIZE - 1 is
shown as the text "<5" (task 9.3), so a small group cannot be picked out.
A count of 0 is shown as 0.

Endpoint: GET /api/reports/monthly?year=YYYY&month=M
Allowed roles: rhu_admin, physician, midwife
Every call is logged to audit_log with action "report_exported".

The module purposely imports nothing from mothers, referrals or risk that
would expose individual records — only aggregate COUNT queries are used.
"""
from common import query_one

# Roles that may access the monthly report
REPORT_ROLES = ("rhu_admin", "physician", "midwife")

# Groups smaller than this are not shown as numbers (task 9.3).
MIN_GROUP_SIZE = 5


def suppress_small(count):
    """Hide small non-zero counts so a few people cannot be identified."""
    if 0 < count < MIN_GROUP_SIZE:
        return f"<{MIN_GROUP_SIZE}"
    return count


# ── Schema ────────────────────────────────────────────────────────────────────

def setup_reports(db):
    """No dedicated tables are needed; the report queries read existing tables.

    This function exists so db.open_database() can call it in the same
    setup_X() pattern used by every other module.
    """


# ── Report logic ──────────────────────────────────────────────────────────────

def monthly_report(db, year, month):
    """Return aggregate counts for the given calendar month.

    Parameters
    ----------
    db    : database connection
    year  : int – four-digit year (e.g. 2025)
    month : int – month number 1-12

    Returns a dict with the following integer-valued keys:

      newMothers          – mothers whose consent was recorded in this month
      prenatalCompleted   – prenatal visits completed in this month
      postnatalCompleted  – postnatal visits completed in this month
      homeVisitsCompleted – home visits completed in this month
      riskFlagsRaised     – risk flags raised in this month
      referralsCreated    – referrals created in this month
      referralsClosed     – referrals closed in this month

    No names, contacts, addresses or any PII are included.
    """
    # Build the YYYY-MM prefix once; SQLite stores timestamps as ISO-8601 text.
    prefix = f"{year:04d}-{month:02d}"
    # Date-range boundaries (inclusive start, exclusive end) for the month.
    # Using LIKE 'YYYY-MM%' is simpler and equally correct for ISO-8601 timestamps.
    like_month = prefix + "%"

    # ── New mothers (consent_recorded_at falls in this month) ────────────────
    new_mothers = query_one(
        db,
        "SELECT COUNT(*) AS cnt FROM mothers WHERE consent_recorded_at LIKE ?",
        (like_month,),
    )["cnt"]

    # ── Prenatal visits completed ─────────────────────────────────────────────
    prenatal_completed = query_one(
        db,
        """SELECT COUNT(*) AS cnt FROM prenatal_visits
           WHERE status = 'completed' AND scheduled_for LIKE ?""",
        (like_month,),
    )["cnt"]

    # ── Postnatal visits completed ────────────────────────────────────────────
    postnatal_completed = query_one(
        db,
        """SELECT COUNT(*) AS cnt FROM postnatal_visits
           WHERE status = 'completed' AND scheduled_for LIKE ?""",
        (like_month,),
    )["cnt"]

    # ── Home visits completed ─────────────────────────────────────────────────
    home_visits_completed = query_one(
        db,
        """SELECT COUNT(*) AS cnt FROM home_visits
           WHERE status = 'completed' AND scheduled_for LIKE ?""",
        (like_month,),
    )["cnt"]

    # ── Risk flags raised ─────────────────────────────────────────────────────
    risk_flags_raised = query_one(
        db,
        "SELECT COUNT(*) AS cnt FROM risk_flags WHERE raised_at LIKE ?",
        (like_month,),
    )["cnt"]

    # ── Referrals created ─────────────────────────────────────────────────────
    referrals_created = query_one(
        db,
        "SELECT COUNT(*) AS cnt FROM referrals WHERE created_at LIKE ?",
        (like_month,),
    )["cnt"]

    # ── Referrals closed (most recent status-change event in this month) ──────
    # A referral is "closed this month" when its status is 'closed' and its
    # updated_at timestamp falls within the requested month.
    referrals_closed = query_one(
        db,
        """SELECT COUNT(*) AS cnt FROM referrals
           WHERE status = 'closed' AND updated_at LIKE ?""",
        (like_month,),
    )["cnt"]

    return {
        "year":                 year,
        "month":                month,
        "newMothers":           suppress_small(new_mothers),
        "prenatalCompleted":    suppress_small(prenatal_completed),
        "postnatalCompleted":   suppress_small(postnatal_completed),
        "homeVisitsCompleted":  suppress_small(home_visits_completed),
        "riskFlagsRaised":      suppress_small(risk_flags_raised),
        "referralsCreated":     suppress_small(referrals_created),
        "referralsClosed":      suppress_small(referrals_closed),
    }
