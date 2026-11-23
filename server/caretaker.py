"""Caretaker workspace data: visits, support tasks and care-team messages for a patient."""
from common import iso_time, query_all, query_one, table_columns, transaction, utc_now
from visits import list_visits

CARETAKER_PATIENT_ID = "MH-2025-0148"  # Maria Santos, the patient this caretaker supports


def setup_caretaker(db):
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS caretaker_tasks (
          id          INTEGER PRIMARY KEY AUTOINCREMENT,
          patient_id  TEXT NOT NULL REFERENCES patients(id),
          title       TEXT NOT NULL,
          done        INTEGER NOT NULL DEFAULT 0,
          done_at     TEXT
        );
        CREATE TABLE IF NOT EXISTS care_messages (
          id          INTEGER PRIMARY KEY AUTOINCREMENT,
          patient_id  TEXT NOT NULL REFERENCES patients(id),
          sender      TEXT NOT NULL,
          sender_role TEXT NOT NULL DEFAULT '',
          sent_at     TEXT NOT NULL,
          body        TEXT NOT NULL,
          is_read     INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    # Migration: databases created before sender_role existed get the column added.
    if "sender_role" not in table_columns(db, "care_messages"):
        db.execute("ALTER TABLE care_messages ADD COLUMN sender_role TEXT NOT NULL DEFAULT ''")

    if query_one(db, "SELECT COUNT(*) AS count FROM caretaker_tasks")["count"] > 0:
        return
    with transaction(db):
        for title in (
            "Remind Maria about the 3 Dec appointment",
            "Arrange transport to the clinic",
            "Check in with Maria after the visit",
        ):
            db.execute("INSERT INTO caretaker_tasks (patient_id, title) VALUES (?, ?)", (CARETAKER_PATIENT_ID, title))
        db.execute(
            "INSERT INTO care_messages (patient_id, sender, sender_role, sent_at, body, is_read) VALUES (?, ?, ?, ?, ?, 0)",
            (
                CARETAKER_PATIENT_ID,
                "Nurse Lena Park",
                "nurse",
                "2026-11-23 09:40",
                "Please remind Maria to bring her appointment card to the 3 Dec visit. Thank you for helping her get there.",
            ),
        )


def get_caretaker_workspace(db, patient_id=CARETAKER_PATIENT_ID):
    patient = query_one(db, "SELECT id, name FROM patients WHERE id = ?", (patient_id,))
    if not patient:
        return None
    appointments = []
    for visit in list_visits(db, patient_id=patient_id):
        appointments.append({
            "id": visit["id"],
            "starts_on": visit["starts_on"],
            "starts_at": visit["starts_at"],
            "title": visit["title"],
            "clinician": visit["clinician"],
            "location": visit["location"] or visit["room"],
            "status": "Completed" if visit["status"] == "completed" else "Confirmed",
        })
    tasks = [
        {**task, "done": bool(task["done"])}
        for task in query_all(
            db, "SELECT id, title, done, done_at FROM caretaker_tasks WHERE patient_id = ? ORDER BY id", (patient_id,)
        )
    ]
    messages = [
        {**message, "read": bool(message["is_read"])}
        for message in query_all(
            db,
            "SELECT id, sender, sender_role, sent_at, body, is_read FROM care_messages WHERE patient_id = ? ORDER BY sent_at DESC, id DESC",
            (patient_id,),
        )
    ]
    return {
        "patient": {"id": patient["id"], "name": patient["name"]},
        "appointments": appointments,
        "tasks": tasks,
        "messages": messages,
    }


def set_task_done(db, task_id, done):
    """Sets a task's done state. Returns the updated task, or None if it doesn't exist."""
    done_at = iso_time(utc_now()) if done else None
    cursor = db.execute(
        "UPDATE caretaker_tasks SET done = ?, done_at = ? WHERE id = ?",
        (1 if done else 0, done_at, task_id),
    )
    if not cursor.rowcount:
        return None
    row = query_one(db, "SELECT id, title, done, done_at FROM caretaker_tasks WHERE id = ?", (task_id,))
    return {**row, "done": bool(row["done"])}


def mark_message_read(db, message_id):
    return db.execute("UPDATE care_messages SET is_read = 1 WHERE id = ?", (message_id,)).rowcount > 0


def send_care_message(db, user, data):
    """Sends a message from the care team to a patient's caretaker. Returns {"message": ...} or {"status", "error"}."""
    text = str(data.get("body") or "").strip()
    patient_id = data.get("patientId")
    if not patient_id:
        return {"status": 400, "error": "Please choose a patient."}
    if not text:
        return {"status": 400, "error": "Please write a message first."}
    if len(text) > 1000:
        return {"status": 400, "error": "Messages can be up to 1,000 characters."}
    if not query_one(db, "SELECT id FROM patients WHERE id = ?", (patient_id,)):
        return {"status": 404, "error": "Patient not found."}
    sent_at = utc_now().strftime("%Y-%m-%d %H:%M")
    cursor = db.execute(
        "INSERT INTO care_messages (patient_id, sender, sender_role, sent_at, body, is_read) VALUES (?, ?, ?, ?, ?, 0)",
        (patient_id, user["name"], user["role"], sent_at, text),
    )
    return {
        "message": {
            "id": cursor.lastrowid,
            "sender": user["name"],
            "sender_role": user["role"],
            "sent_at": sent_at,
            "body": text,
            "read": False,
        }
    }
