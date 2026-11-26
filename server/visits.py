"""Visits: appointments shown on the clinic schedule and on each caretaker's page."""
from common import DATE_RE, TIME_RE, add_days, query_all, query_one, table_columns, text, transaction

BOARD_WEEK_START = "2026-11-30"  # Monday of the week shown on the schedule board

# Starting visits, matching the schedule board and the caretaker's page.
# day_offset: 0 = Monday ... 4 = Friday of the board week.
SEED_VISITS = [
    {"day_offset": 0, "time": "08:15", "patient_id": "PT-1052", "patient_name": "Sofia Marchetti", "title": "First trimester screening", "room": "Room 2", "status": "completed"},
    {"day_offset": 0, "time": "09:00", "patient_id": "PT-1038", "patient_name": "Amara Bello", "title": "BP review", "room": "Room 2", "status": "completed"},
    {"day_offset": 0, "time": "10:30", "patient_id": "MH-2025-0148", "patient_name": "Maria Santos", "title": "28-week prenatal check", "room": "Room 4"},
    {"day_offset": 0, "time": "11:45", "patient_id": "PT-1029", "patient_name": "Priya Raman", "title": "Routine prenatal check", "room": "Room 1"},
    {"day_offset": 0, "time": "14:15", "patient_id": "PT-1046", "patient_name": "Hannah Cole", "title": "Postpartum follow-up", "room": "Room 3"},
    {"day_offset": 1, "time": "08:30", "patient_id": None, "patient_name": "Nadia Haddad", "title": "36-week position check", "room": "Room 1"},
    {"day_offset": 1, "time": "09:15", "patient_id": None, "patient_name": "Grace Lim", "title": "Gestational diabetes review", "room": "Room 2"},
    {"day_offset": 1, "time": "10:00", "patient_id": "PT-1029", "patient_name": "Priya Raman", "title": "Iron level follow-up", "room": "Room 3"},
    {"day_offset": 1, "time": "11:30", "patient_id": None, "patient_name": "Zoe Whitfield", "title": "16-week prenatal check", "room": "Room 2"},
    {"day_offset": 1, "time": "13:45", "patient_id": None, "patient_name": "Fatima Noor", "title": "Growth scan", "room": "Imaging"},
    {"day_offset": 1, "time": "15:00", "patient_id": None, "patient_name": "Claire Dubois", "title": "Birth plan review", "room": "Room 4"},
    {"day_offset": 2, "time": "09:00", "patient_id": "PT-1038", "patient_name": "Amara Bello", "title": "BP monitoring", "room": "Room 4"},
    {"day_offset": 2, "time": "10:15", "patient_id": "MH-2025-0148", "patient_name": "Maria Santos", "title": "Antenatal education", "room": "Room 1"},
    {"day_offset": 2, "time": "13:30", "patient_id": "PT-1052", "patient_name": "Sofia Marchetti", "title": "Screening results", "room": "Room 2"},
    {"day_offset": 2, "time": "15:45", "patient_id": "PT-1029", "patient_name": "Priya Raman", "title": "Routine prenatal check", "room": "Room 3"},
    {"day_offset": 3, "time": "08:00", "patient_id": "PT-1046", "patient_name": "Hannah Cole", "title": "Postpartum follow-up", "room": "Room 3"},
    {"day_offset": 3, "time": "08:45", "patient_id": None, "patient_name": "Grace Lim", "title": "Insulin adjustment", "room": "Room 2"},
    {"day_offset": 3, "time": "09:30", "patient_id": None, "patient_name": "Nadia Haddad", "title": "36-week position check", "room": "Room 1"},
    {"day_offset": 3, "time": "10:30", "patient_id": None, "patient_name": "Zoe Whitfield", "title": "Anatomy scan review", "room": "Imaging"},
    {"day_offset": 3, "time": "11:15", "patient_id": None, "patient_name": "Fatima Noor", "title": "Routine prenatal check", "room": "Room 4"},
    {"day_offset": 3, "time": "13:00", "patient_id": None, "patient_name": "Claire Dubois", "title": "Feeding support", "room": "Room 2"},
    {"day_offset": 3, "time": "14:30", "patient_id": "PT-1038", "patient_name": "Amara Bello", "title": "BP recheck", "room": "Room 2"},
    {"day_offset": 4, "time": "09:15", "patient_id": "MH-2025-0148", "patient_name": "Maria Santos", "title": "28-week prenatal check", "room": "Room 4"},
    {"day_offset": 4, "time": "10:45", "patient_id": "PT-1052", "patient_name": "Sofia Marchetti", "title": "First trimester screening", "room": "Room 2"},
    {"day_offset": 4, "time": "13:15", "patient_id": "PT-1029", "patient_name": "Priya Raman", "title": "Routine prenatal check", "room": "Room 1"},
]

# Caretaker's own dated visits for Maria Santos (upcoming, shown on the caretaker page).
SEED_CARETAKER_VISITS = [
    {"starts_on": "2026-12-03", "time": "09:00", "patient_id": "MH-2025-0148", "patient_name": "Maria Santos", "title": "Prenatal check-up", "clinician": "Dr. Aisha Rahman", "location": "Bayview Women's Health, Room 4"},
    {"starts_on": "2026-12-10", "time": "14:00", "patient_id": "MH-2025-0148", "patient_name": "Maria Santos", "title": "Antenatal class", "clinician": "", "location": "Community Room, Bayview Women's Health"},
]

INSERT_SEED = """
    INSERT INTO visits (patient_id, patient_name, starts_on, starts_at, title, clinician, location, room, status, created_by)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'seed')
"""


def setup_visits(db):
    db.execute("DROP TABLE IF EXISTS appointments")  # replaced by visits (only earlier sample rows)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS visits (
          id            INTEGER PRIMARY KEY AUTOINCREMENT,
          patient_id    TEXT,
          patient_name  TEXT NOT NULL,
          starts_on     TEXT NOT NULL,
          starts_at     TEXT NOT NULL,
          title         TEXT NOT NULL,
          clinician     TEXT NOT NULL DEFAULT '',
          location      TEXT NOT NULL DEFAULT '',
          room          TEXT NOT NULL DEFAULT '',
          status        TEXT NOT NULL DEFAULT 'scheduled',
          created_by    TEXT NOT NULL DEFAULT ''
        )
        """
    )
    if query_one(db, "SELECT COUNT(*) AS count FROM visits")["count"] > 0:
        return
    with transaction(db):
        for v in SEED_VISITS:
            db.execute(INSERT_SEED, (
                v["patient_id"], v["patient_name"], add_days(BOARD_WEEK_START, v["day_offset"]),
                v["time"], v["title"], "", "", v["room"], v.get("status", "scheduled"),
            ))
        for v in SEED_CARETAKER_VISITS:
            db.execute(INSERT_SEED, (
                v["patient_id"], v["patient_name"], v["starts_on"], v["time"], v["title"],
                v["clinician"], v["location"], "", "Confirmed",
            ))


VISIT_COLUMNS = "id, patient_id, patient_name, starts_on, starts_at, title, clinician, location, room, status, created_by"


def list_visits(db, patient_id=None, date_from=None, date_to=None):
    where = []
    values = []
    if patient_id:
        where.append("patient_id = ?")
        values.append(patient_id)
    if date_from:
        where.append("starts_on >= ?")
        values.append(date_from)
    if date_to:
        where.append("starts_on <= ?")
        values.append(date_to)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return query_all(db, f"SELECT {VISIT_COLUMNS} FROM visits {clause} ORDER BY starts_on, starts_at", values)


def get_visit(db, visit_id):
    return query_one(db, f"SELECT {VISIT_COLUMNS} FROM visits WHERE id = ?", (visit_id,))


def create_visit(db, user, data):
    """Validates and creates a visit. Returns {"visit": ...} or {"status", "error"}."""
    patient_name = text(data.get("patientName"))
    starts_on = str(data.get("startsOn") or "")
    starts_at = str(data.get("startsAt") or "")
    title = text(data.get("title"))
    if not patient_name:
        return {"status": 400, "error": "Please choose or enter a patient name."}
    if not DATE_RE.fullmatch(starts_on):
        return {"status": 400, "error": "Please choose a date."}
    if not TIME_RE.fullmatch(starts_at):
        return {"status": 400, "error": "Please choose a time."}
    if not title:
        return {"status": 400, "error": "Please enter the visit type."}
    cursor = db.execute(
        """
        INSERT INTO visits (patient_id, patient_name, starts_on, starts_at, title, clinician, location, room, status, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'scheduled', ?)
        """,
        (
            data.get("patientId") or None,
            patient_name,
            starts_on,
            starts_at,
            title,
            str(data.get("clinician") or ""),
            str(data.get("location") or ""),
            str(data.get("room") or ""),
            user["email"],
        ),
    )
    return {"visit": get_visit(db, cursor.lastrowid)}


def update_visit(db, visit_id, changes):
    """Moves a visit to another day in the board week, or sets its status. Returns the visit, or None."""
    if not get_visit(db, visit_id):
        return None
    starts_on = changes.get("startsOn")
    if isinstance(starts_on, str) and DATE_RE.fullmatch(starts_on):
        db.execute("UPDATE visits SET starts_on = ? WHERE id = ?", (starts_on, visit_id))
    status = changes.get("status")
    if isinstance(status, str) and status in ("scheduled", "completed"):
        db.execute("UPDATE visits SET status = ? WHERE id = ?", (status, visit_id))
    return get_visit(db, visit_id)
