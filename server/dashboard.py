"""Dashboard summary queries for the BHW and RHU workspaces (task 9.1).

Returns pre-aggregated counts so the front-end can replace hardcoded sample
numbers with real data from the database.  No calculations or thresholds are
performed here — only plain COUNT/GROUP BY queries.

BHW dashboard  — GET /api/bhw/dashboard
    Counts for the signed-in BHW's scope:
      - mothers by status (prenatal, postpartum, closed)
      - home visits: due today, completed, missed, rescheduled
      - open risk flags raised by or for the BHW's mothers
      - pending referrals for the BHW's mothers

RHU dashboard  — GET /api/rhu/dashboard
    Network-wide counts for the RHU admin view:
      - registered pregnant (prenatal) mothers
      - registered postpartum mothers
      - mothers under prenatal monitoring (at least one scheduled prenatal visit)
      - postpartum mothers needing follow-up (open referral or open risk flag)
      - open risk flags
      - referrals by status
      - overdue referrals (pending beyond the protocol window)
"""

from datetime import date as _date

from common import query_all, query_one, sql_time, utc_now
from referrals import DEFAULT_OVERDUE_HOURS


# ── BHW dashboard ─────────────────────────────────────────────────────────────

def bhw_dashboard(db, bhw_id, today=None):
    """Return summary counts for the signed-in BHW.

    Parameters
    ----------
    db      : database connection
    bhw_id  : int – the signed-in BHW's user id
    today   : str (YYYY-MM-DD) for testing; defaults to today's local date

    Returns a dict:
    {
      "mothers": {"prenatal": int, "postpartum": int, "closed": int, "total": int},
      "homeVisits": {
          "dueToday": int, "overdue": int, "missed": int,
          "completed": int, "rescheduled": int
      },
      "openFlags": int,
      "pendingReferrals": int,
    }
    """
    if today is None:
        today = _date.today().isoformat()

    # ── Mothers assigned to this BHW ─────────────────────────────────────────
    mother_rows = query_all(
        db,
        """SELECT status, COUNT(*) AS cnt
           FROM mothers
           WHERE assigned_bhw_id = ?
           GROUP BY status""",
        (bhw_id,),
    )
    mothers = {"prenatal": 0, "postpartum": 0, "closed": 0}
    for r in mother_rows:
        if r["status"] in mothers:
            mothers[r["status"]] = r["cnt"]
    mothers["total"] = sum(mothers.values())

    # ── Home visits ───────────────────────────────────────────────────────────
    # "Due today" = planned/rescheduled with scheduled_for on today's date
    due_today = query_one(
        db,
        """SELECT COUNT(*) AS cnt FROM home_visits
           WHERE bhw_id = ?
             AND status IN ('planned', 'rescheduled')
             AND scheduled_for >= ?
             AND scheduled_for < ?""",
        (bhw_id, today, today + "T99:99"),
    )["cnt"]

    # "Overdue" = planned/rescheduled with scheduled_for before today
    overdue = query_one(
        db,
        """SELECT COUNT(*) AS cnt FROM home_visits
           WHERE bhw_id = ?
             AND status IN ('planned', 'rescheduled')
             AND scheduled_for < ?""",
        (bhw_id, today),
    )["cnt"]

    missed = query_one(
        db,
        "SELECT COUNT(*) AS cnt FROM home_visits WHERE bhw_id = ? AND status = 'missed'",
        (bhw_id,),
    )["cnt"]

    completed = query_one(
        db,
        "SELECT COUNT(*) AS cnt FROM home_visits WHERE bhw_id = ? AND status = 'completed'",
        (bhw_id,),
    )["cnt"]

    rescheduled_total = query_one(
        db,
        """SELECT COUNT(*) AS cnt FROM home_visits
           WHERE bhw_id = ?
             AND status = 'rescheduled'
             AND scheduled_for < ?""",
        (bhw_id, today),
    )["cnt"]

    home_visits = {
        "dueToday":   due_today,
        "overdue":    overdue,
        "missed":     missed,
        "completed":  completed,
        "rescheduled": rescheduled_total,
    }

    # ── Open risk flags raised for this BHW's mothers ─────────────────────────
    open_flags = query_one(
        db,
        """SELECT COUNT(*) AS cnt FROM risk_flags rf
           JOIN mothers m ON m.id = rf.mother_id
           WHERE m.assigned_bhw_id = ?
             AND rf.status = 'open'""",
        (bhw_id,),
    )["cnt"]

    # ── Pending referrals for this BHW's mothers ──────────────────────────────
    pending_referrals = query_one(
        db,
        """SELECT COUNT(*) AS cnt FROM referrals r
           JOIN mothers m ON m.id = r.mother_id
           WHERE m.assigned_bhw_id = ?
             AND r.status = 'pending'""",
        (bhw_id,),
    )["cnt"]

    return {
        "mothers":          mothers,
        "homeVisits":       home_visits,
        "openFlags":        open_flags,
        "pendingReferrals": pending_referrals,
    }


# ── RHU dashboard ─────────────────────────────────────────────────────────────

def rhu_dashboard(db, now=None):
    """Return network-wide summary counts for the RHU admin view.

    Parameters
    ----------
    db  : database connection
    now : datetime (UTC) for testing; defaults to utc_now()

    Returns a dict:
    {
      "registeredPrenatal": int,
      "registeredPostpartum": int,
      "underPrenatalMonitoring": int,
      "postpartumFollowUp": int,
      "openFlags": int,
      "referralsByStatus": {
          "pending": int, "acknowledged": int, "attended": int, "closed": int
      },
      "overdueReferrals": int,
    }
    """
    if now is None:
        now = utc_now()
    now_str = sql_time(now)

    # ── Mothers by status ─────────────────────────────────────────────────────
    registered_prenatal = query_one(
        db,
        "SELECT COUNT(*) AS cnt FROM mothers WHERE status = 'prenatal'",
    )["cnt"]

    registered_postpartum = query_one(
        db,
        "SELECT COUNT(*) AS cnt FROM mothers WHERE status = 'postpartum'",
    )["cnt"]

    # ── Under prenatal monitoring — has at least one scheduled/rescheduled
    #    prenatal visit ─────────────────────────────────────────────────────────
    under_prenatal = query_one(
        db,
        """SELECT COUNT(DISTINCT mother_id) AS cnt
           FROM prenatal_visits
           WHERE status IN ('scheduled', 'rescheduled')""",
    )["cnt"]

    # ── Postpartum mothers needing follow-up — has an open referral or open flag
    postpartum_follow_up = query_one(
        db,
        """SELECT COUNT(DISTINCT m.id) AS cnt
           FROM mothers m
           WHERE m.status = 'postpartum'
             AND (
               EXISTS (
                 SELECT 1 FROM referrals r
                 WHERE r.mother_id = m.id
                   AND r.status != 'closed'
               )
               OR EXISTS (
                 SELECT 1 FROM risk_flags rf
                 WHERE rf.mother_id = m.id
                   AND rf.status = 'open'
               )
             )""",
    )["cnt"]

    # ── Open risk flags ───────────────────────────────────────────────────────
    open_flags = query_one(
        db,
        "SELECT COUNT(*) AS cnt FROM risk_flags WHERE status = 'open'",
    )["cnt"]

    # ── Referrals by status ───────────────────────────────────────────────────
    referral_rows = query_all(
        db,
        """SELECT status, COUNT(*) AS cnt FROM referrals GROUP BY status""",
    )
    referrals_by_status = {"pending": 0, "acknowledged": 0, "attended": 0, "closed": 0}
    for r in referral_rows:
        if r["status"] in referrals_by_status:
            referrals_by_status[r["status"]] = r["cnt"]

    # ── Overdue referrals (pending past acknowledgement window) ───────────────
    overdue_referrals = query_one(
        db,
        """SELECT COUNT(*) AS cnt FROM referrals
           WHERE status = 'pending'
             AND (
               (due_by IS NOT NULL AND due_by < ?)
               OR
               (due_by IS NULL
                AND datetime(created_at, ? || ' hours') < ?)
             )""",
        (now_str, str(DEFAULT_OVERDUE_HOURS), now_str),
    )["cnt"]

    return {
        "registeredPrenatal":      registered_prenatal,
        "registeredPostpartum":    registered_postpartum,
        "underPrenatalMonitoring": under_prenatal,
        "postpartumFollowUp":      postpartum_follow_up,
        "openFlags":               open_flags,
        "referralsByStatus":       referrals_by_status,
        "overdueReferrals":        overdue_referrals,
    }
