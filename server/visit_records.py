"""Prenatal, postnatal and home visit records for the BHW-RHU monitoring system.

Tables
------
prenatal_visits   — clinic-side prenatal check-ups
postnatal_visits  — clinic-side postnatal check-ups
home_visits       — BHW field visits with checklist answers

Design rules enforced here
--------------------------
- status transitions are limited to the allowed set (design.md §4)
- Visits past their scheduled_for time and not completed become 'missed'
  when mark_overdue_visits() is called (task 3.3; no background job needed)
- home_visits.checklist_answers is stored as JSON; no scores are computed
- vitals (blood_pressure, weight, fetal_heart_rate) are stored as a JSON blob;
  interpretation is left to the reviewing professional
"""

import json
from datetime import date, datetime, timezone

from common import query_all, query_one, sql_time, text, transaction, utc_now
import reminders as reminders_module

# ── Status sets ───────────────────────────────────────────────────────────────

CLINIC_STATUSES = ("scheduled", "completed", "missed", "rescheduled")
HOME_STATUSES   = ("planned",   "completed", "missed", "rescheduled")

# Which transitions are allowed from each status
CLINIC_TRANSITIONS = {
    "scheduled":   {"completed", "missed", "rescheduled"},
    "rescheduled": {"completed", "missed", "rescheduled"},
    "completed":   set(),   # terminal
    "missed":      {"rescheduled"},
}
HOME_TRANSITIONS = {
    "planned":     {"completed", "missed", "rescheduled"},
    "rescheduled": {"completed", "missed", "rescheduled"},
    "completed":   set(),
    "missed":      {"rescheduled"},
}


# ── Schema ────────────────────────────────────────────────────────────────────

def setup_visit_records(db):
    _create_prenatal_visits(db)
    _create_postnatal_visits(db)
    _create_home_visits(db)


def _create_prenatal_visits(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS prenatal_visits (
          id            INTEGER PRIMARY KEY AUTOINCREMENT,
          mother_id     INTEGER NOT NULL REFERENCES mothers(id),
          scheduled_for TEXT    NOT NULL,
          status        TEXT    NOT NULL DEFAULT 'scheduled'
                          CHECK (status IN ('scheduled','completed','missed','rescheduled')),
          performed_by  INTEGER REFERENCES users(id),
          vitals        TEXT    NOT NULL DEFAULT '{}',
          observations  TEXT    NOT NULL DEFAULT '',
          created_by    INTEGER NOT NULL REFERENCES users(id),
          created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
          updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)


def _create_postnatal_visits(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS postnatal_visits (
          id            INTEGER PRIMARY KEY AUTOINCREMENT,
          mother_id     INTEGER NOT NULL REFERENCES mothers(id),
          scheduled_for TEXT    NOT NULL,
          status        TEXT    NOT NULL DEFAULT 'scheduled'
                          CHECK (status IN ('scheduled','completed','missed','rescheduled')),
          performed_by  INTEGER REFERENCES users(id),
          vitals        TEXT    NOT NULL DEFAULT '{}',
          observations  TEXT    NOT NULL DEFAULT '',
          created_by    INTEGER NOT NULL REFERENCES users(id),
          created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
          updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)


def _create_home_visits(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS home_visits (
          id                  INTEGER PRIMARY KEY AUTOINCREMENT,
          mother_id           INTEGER NOT NULL REFERENCES mothers(id),
          bhw_id              INTEGER NOT NULL REFERENCES users(id),
          scheduled_for       TEXT    NOT NULL,
          status              TEXT    NOT NULL DEFAULT 'planned'
                                CHECK (status IN ('planned','completed','missed','rescheduled')),
          checklist_id        INTEGER,
          checklist_answers   TEXT    NOT NULL DEFAULT '{}',
          notes               TEXT    NOT NULL DEFAULT '',
          submitted_to_rhu_at TEXT,
          created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
          updated_at          TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)


# ── Serialisation helpers ─────────────────────────────────────────────────────

def _clinic_row(row):
    return {
        "id":           row["id"],
        "motherId":     row["mother_id"],
        "scheduledFor": row["scheduled_for"],
        "status":       row["status"],
        "performedBy":  row["performed_by"],
        "vitals":       json.loads(row["vitals"] or "{}"),
        "observations": row["observations"],
        "createdBy":    row["created_by"],
        "createdAt":    row["created_at"],
        "updatedAt":    row["updated_at"],
    }


def _home_row(row):
    return {
        "id":               row["id"],
        "motherId":         row["mother_id"],
        "bhwId":            row["bhw_id"],
        "scheduledFor":     row["scheduled_for"],
        "status":           row["status"],
        "checklistId":      row["checklist_id"],
        "checklistAnswers": json.loads(row["checklist_answers"] or "{}"),
        "notes":            row["notes"],
        "submittedToRhuAt": row["submitted_to_rhu_at"],
        "createdAt":        row["created_at"],
        "updatedAt":        row["updated_at"],
    }


# ── Missed-visit marking (task 3.3) ──────────────────────────────────────────

def mark_overdue_visits(db, now=None):
    """Mark any visit whose scheduled_for has passed and is still in an
    open status as 'missed'.  Call this on each dashboard load.

    *now* is a datetime for testing; defaults to UTC now.
    Returns the total number of rows updated.
    """
    cutoff = sql_time(now if now is not None else utc_now())
    total = 0
    for table, open_statuses in (
        ("prenatal_visits",  ("scheduled", "rescheduled")),
        ("postnatal_visits", ("scheduled", "rescheduled")),
        ("home_visits",      ("planned",   "rescheduled")),
    ):
        placeholders = ",".join("?" for _ in open_statuses)
        cur = db.execute(
            f"""UPDATE {table}
                SET status = 'missed', updated_at = datetime('now')
                WHERE scheduled_for < ?
                  AND status IN ({placeholders})""",
            (cutoff, *open_statuses),
        )
        total += cur.rowcount
    return total


# ── Prenatal visits ───────────────────────────────────────────────────────────

def list_prenatal(db, mother_id):
    rows = query_all(
        db,
        "SELECT * FROM prenatal_visits WHERE mother_id = ? ORDER BY scheduled_for",
        (mother_id,),
    )
    return [_clinic_row(r) for r in rows]


def create_prenatal(db, user, mother_id, data):
    """Schedule a prenatal visit.  Returns {"visit": ...} or {"status","error"}."""
    scheduled_for = text(data.get("scheduledFor") or data.get("scheduled_for") or "")
    if not scheduled_for:
        return {"status": 400, "error": "scheduledFor is required."}
    vitals = data.get("vitals") or {}
    if not isinstance(vitals, dict):
        return {"status": 400, "error": "vitals must be an object."}
    observations = text(data.get("observations") or "")

    with transaction(db):
        cur = db.execute(
            """INSERT INTO prenatal_visits
               (mother_id, scheduled_for, vitals, observations, created_by)
               VALUES (?,?,?,?,?)""",
            (mother_id, scheduled_for, json.dumps(vitals), observations, user["id"]),
        )
        vid = cur.lastrowid
    row = query_one(db, "SELECT * FROM prenatal_visits WHERE id = ?", (vid,))
    try:
        reminders_module.create_reminder(db, {
            "mother_id": mother_id,
            "user_id":   user["id"],
            "channel":   "in_app",
            "message":   f"Prenatal visit scheduled for {scheduled_for}",
            "send_at":   scheduled_for,
        })
    except Exception:
        pass
    return {"visit": _clinic_row(row)}


def update_prenatal(db, user, visit_id, data):
    return _update_clinic_visit(db, user, "prenatal_visits", visit_id, data, CLINIC_TRANSITIONS)


def create_postnatal(db, user, mother_id, data):
    """Schedule a postnatal visit.  Returns {"visit": ...} or {"status","error"}."""
    scheduled_for = text(data.get("scheduledFor") or data.get("scheduled_for") or "")
    if not scheduled_for:
        return {"status": 400, "error": "scheduledFor is required."}
    vitals = data.get("vitals") or {}
    if not isinstance(vitals, dict):
        return {"status": 400, "error": "vitals must be an object."}
    observations = text(data.get("observations") or "")

    with transaction(db):
        cur = db.execute(
            """INSERT INTO postnatal_visits
               (mother_id, scheduled_for, vitals, observations, created_by)
               VALUES (?,?,?,?,?)""",
            (mother_id, scheduled_for, json.dumps(vitals), observations, user["id"]),
        )
        vid = cur.lastrowid
    row = query_one(db, "SELECT * FROM postnatal_visits WHERE id = ?", (vid,))
    try:
        reminders_module.create_reminder(db, {
            "mother_id": mother_id,
            "user_id":   user["id"],
            "channel":   "in_app",
            "message":   f"Postnatal visit scheduled for {scheduled_for}",
            "send_at":   scheduled_for,
        })
    except Exception:
        pass
    return {"visit": _clinic_row(row)}


def update_postnatal(db, user, visit_id, data):
    return _update_clinic_visit(db, user, "postnatal_visits", visit_id, data, CLINIC_TRANSITIONS)


def list_postnatal(db, mother_id):
    rows = query_all(
        db,
        "SELECT * FROM postnatal_visits WHERE mother_id = ? ORDER BY scheduled_for",
        (mother_id,),
    )
    return [_clinic_row(r) for r in rows]


def _update_clinic_visit(db, user, table, visit_id, data, transitions):
    """Shared update logic for prenatal and postnatal visits."""
    row = query_one(db, f"SELECT * FROM {table} WHERE id = ?", (visit_id,))
    if not row:
        return {"status": 404, "error": "Visit not found."}

    new_status   = data.get("status")
    observations = data.get("observations")
    vitals       = data.get("vitals")
    performed_by = data.get("performedBy") or data.get("performed_by")

    updates = []
    values  = []

    if new_status is not None:
        allowed = transitions.get(row["status"], set())
        if new_status not in CLINIC_STATUSES:
            return {"status": 400,
                    "error": f"Invalid status '{new_status}'. Must be one of: {', '.join(CLINIC_STATUSES)}."}
        if new_status not in allowed:
            return {"status": 400,
                    "error": f"Cannot move from '{row['status']}' to '{new_status}'."}
        updates.append("status = ?")
        values.append(new_status)

    if observations is not None:
        updates.append("observations = ?")
        values.append(text(observations))

    if vitals is not None:
        if not isinstance(vitals, dict):
            return {"status": 400, "error": "vitals must be an object."}
        updates.append("vitals = ?")
        values.append(json.dumps(vitals))

    if performed_by is not None:
        updates.append("performed_by = ?")
        values.append(performed_by)

    if not updates:
        updated = query_one(db, f"SELECT * FROM {table} WHERE id = ?", (visit_id,))
        return {"visit": _clinic_row(updated)}

    updates.append("updated_at = datetime('now')")
    db.execute(
        f"UPDATE {table} SET {', '.join(updates)} WHERE id = ?",
        [*values, visit_id],
    )
    updated = query_one(db, f"SELECT * FROM {table} WHERE id = ?", (visit_id,))
    return {"visit": _clinic_row(updated)}


# ── Home visits ───────────────────────────────────────────────────────────────

def create_home_visit(db, user, mother_id, data):
    """Plan a home visit.  Returns {"visit": ...} or {"status","error"}."""
    scheduled_for = text(data.get("scheduledFor") or data.get("scheduled_for") or "")
    if not scheduled_for:
        return {"status": 400, "error": "scheduledFor is required."}
    notes_text   = text(data.get("notes") or "")
    checklist_id = data.get("checklistId") or data.get("checklist_id")
    answers      = data.get("checklistAnswers") or data.get("checklist_answers") or {}
    if not isinstance(answers, dict):
        return {"status": 400, "error": "checklistAnswers must be an object."}

    with transaction(db):
        cur = db.execute(
            """INSERT INTO home_visits
               (mother_id, bhw_id, scheduled_for, checklist_id, checklist_answers, notes)
               VALUES (?,?,?,?,?,?)""",
            (mother_id, user["id"], scheduled_for,
             checklist_id, json.dumps(answers), notes_text),
        )
        vid = cur.lastrowid
    row = query_one(db, "SELECT * FROM home_visits WHERE id = ?", (vid,))
    try:
        reminders_module.create_reminder(db, {
            "mother_id": mother_id,
            "user_id":   user["id"],
            "channel":   "in_app",
            "message":   f"Home visit planned for {scheduled_for}",
            "send_at":   scheduled_for,
        })
    except Exception:
        pass
    return {"visit": _home_row(row)}


def update_home_visit(db, user, visit_id, data):
    """Update status, notes or checklist answers for a home visit."""
    row = query_one(db, "SELECT * FROM home_visits WHERE id = ?", (visit_id,))
    if not row:
        return {"status": 404, "error": "Home visit not found."}

    new_status = data.get("status")
    notes_text = data.get("notes")
    answers    = data.get("checklistAnswers") or data.get("checklist_answers")

    updates = []
    values  = []

    if new_status is not None:
        allowed = HOME_TRANSITIONS.get(row["status"], set())
        if new_status not in HOME_STATUSES:
            return {"status": 400,
                    "error": f"Invalid status '{new_status}'. Must be one of: {', '.join(HOME_STATUSES)}."}
        if new_status not in allowed:
            return {"status": 400,
                    "error": f"Cannot move from '{row['status']}' to '{new_status}'."}
        updates.append("status = ?")
        values.append(new_status)

    if notes_text is not None:
        updates.append("notes = ?")
        values.append(text(notes_text))

    if answers is not None:
        if not isinstance(answers, dict):
            return {"status": 400, "error": "checklistAnswers must be an object."}
        updates.append("checklist_answers = ?")
        values.append(json.dumps(answers))

    if not updates:
        return {"visit": _home_row(row)}

    updates.append("updated_at = datetime('now')")
    db.execute(
        f"UPDATE home_visits SET {', '.join(updates)} WHERE id = ?",
        [*values, visit_id],
    )
    updated = query_one(db, "SELECT * FROM home_visits WHERE id = ?", (visit_id,))
    return {"visit": _home_row(updated)}


def list_home_visits(db, mother_id):
    rows = query_all(
        db,
        "SELECT * FROM home_visits WHERE mother_id = ? ORDER BY scheduled_for",
        (mother_id,),
    )
    return [_home_row(r) for r in rows]


# ── BHW schedule (task 3.4) ───────────────────────────────────────────────────

def bhw_schedule(db, bhw_id, now=None):
    """Return due-today, overdue and missed visits for *bhw_id*.

    Covers home_visits (filtered by bhw_id) and prenatal/postnatal visits for
    mothers assigned to this BHW (mothers.assigned_bhw_id = bhw_id).

    Calls mark_overdue_visits() first so the lists are always up to date.
    *now* is a datetime for testing; defaults to UTC now.
    """
    mark_overdue_visits(db, now=now)
    if now is not None:
        today_str = now.strftime("%Y-%m-%d")
    else:
        # Use local date (not UTC) so the BHW's day boundaries are correct
        # regardless of the server's timezone offset.
        from datetime import date as _date
        today_str = _date.today().isoformat()

    # ── home visits ───────────────────────────────────────────────────────────

    hv_due_today = query_all(
        db,
        """SELECT * FROM home_visits
           WHERE bhw_id = ?
             AND status IN ('planned','rescheduled')
             AND scheduled_for >= ?
             AND scheduled_for < ?
           ORDER BY scheduled_for""",
        (bhw_id, today_str, today_str + "T99:99"),
    )
    hv_overdue = query_all(
        db,
        """SELECT * FROM home_visits
           WHERE bhw_id = ?
             AND status IN ('planned','rescheduled')
             AND scheduled_for < ?
           ORDER BY scheduled_for""",
        (bhw_id, today_str),
    )
    hv_missed = query_all(
        db,
        """SELECT * FROM home_visits
           WHERE bhw_id = ?
             AND status = 'missed'
           ORDER BY scheduled_for DESC""",
        (bhw_id,),
    )

    def _tagged_home(rows, category):
        result = []
        for r in rows:
            d = _home_row(r)
            d["visitType"] = "home"
            d["category"]  = category
            result.append(d)
        return result

    # ── clinic visits (prenatal + postnatal) for the BHW's assigned mothers ──

    def _tagged_clinic(rows, visit_type, category):
        result = []
        for r in rows:
            d = _clinic_row(r)
            d["visitType"] = visit_type
            d["category"]  = category
            result.append(d)
        return result

    # prenatal – due today
    pn_due_today = query_all(
        db,
        """SELECT pv.* FROM prenatal_visits pv
           JOIN mothers m ON m.id = pv.mother_id
           WHERE m.assigned_bhw_id = ?
             AND pv.status IN ('scheduled','rescheduled')
             AND pv.scheduled_for >= ?
             AND pv.scheduled_for < ?
           ORDER BY pv.scheduled_for""",
        (bhw_id, today_str, today_str + "T99:99"),
    )
    # prenatal – overdue (open but before today, not yet auto-marked missed)
    pn_overdue = query_all(
        db,
        """SELECT pv.* FROM prenatal_visits pv
           JOIN mothers m ON m.id = pv.mother_id
           WHERE m.assigned_bhw_id = ?
             AND pv.status IN ('scheduled','rescheduled')
             AND pv.scheduled_for < ?
           ORDER BY pv.scheduled_for""",
        (bhw_id, today_str),
    )
    # prenatal – missed
    pn_missed = query_all(
        db,
        """SELECT pv.* FROM prenatal_visits pv
           JOIN mothers m ON m.id = pv.mother_id
           WHERE m.assigned_bhw_id = ?
             AND pv.status = 'missed'
           ORDER BY pv.scheduled_for DESC""",
        (bhw_id,),
    )

    # postnatal – due today
    po_due_today = query_all(
        db,
        """SELECT pv.* FROM postnatal_visits pv
           JOIN mothers m ON m.id = pv.mother_id
           WHERE m.assigned_bhw_id = ?
             AND pv.status IN ('scheduled','rescheduled')
             AND pv.scheduled_for >= ?
             AND pv.scheduled_for < ?
           ORDER BY pv.scheduled_for""",
        (bhw_id, today_str, today_str + "T99:99"),
    )
    # postnatal – overdue
    po_overdue = query_all(
        db,
        """SELECT pv.* FROM postnatal_visits pv
           JOIN mothers m ON m.id = pv.mother_id
           WHERE m.assigned_bhw_id = ?
             AND pv.status IN ('scheduled','rescheduled')
             AND pv.scheduled_for < ?
           ORDER BY pv.scheduled_for""",
        (bhw_id, today_str),
    )
    # postnatal – missed
    po_missed = query_all(
        db,
        """SELECT pv.* FROM postnatal_visits pv
           JOIN mothers m ON m.id = pv.mother_id
           WHERE m.assigned_bhw_id = ?
             AND pv.status = 'missed'
           ORDER BY pv.scheduled_for DESC""",
        (bhw_id,),
    )

    due_today = (
        _tagged_home(hv_due_today, "dueToday")
        + _tagged_clinic(pn_due_today, "prenatal", "dueToday")
        + _tagged_clinic(po_due_today, "postnatal", "dueToday")
    )
    overdue = (
        _tagged_home(hv_overdue, "overdue")
        + _tagged_clinic(pn_overdue, "prenatal", "overdue")
        + _tagged_clinic(po_overdue, "postnatal", "overdue")
    )
    missed = (
        _tagged_home(hv_missed, "missed")
        + _tagged_clinic(pn_missed, "prenatal", "missed")
        + _tagged_clinic(po_missed, "postnatal", "missed")
    )

    # Sort combined lists by scheduledFor for a consistent ordering
    for lst in (due_today, overdue, missed):
        lst.sort(key=lambda v: v.get("scheduledFor") or "")

    return {
        "dueToday": due_today,
        "overdue":  overdue,
        "missed":   missed,
    }
