"""SQLite database for the care-team workspace (Python's built-in sqlite3, no packages needed)."""
import json
import os
from pathlib import Path

from common import open_connection, query_all, query_one, transaction
import users
import sessions
import audit
import visits
import caretaker
import notes

HERE = Path(__file__).resolve().parent
DEFAULT_DB_PATH = HERE.parent / "data" / "caap.db"
SEED_PATH = HERE / "seed-patients.json"

# Maps the front-end field names to database column names.
FIELD_COLUMNS = {
    "name": "name",
    "displayId": "display_id",
    "stage": "stage",
    "gestation": "gestation",
    "bloodPressure": "blood_pressure",
    "weight": "weight",
    "update": "update_text",
    "appointment": "appointment",
    "status": "status",
    "flag": "flag",
    "note": "note",
    "history": "history",
}

# Fields the API is allowed to change.
EDITABLE_FIELDS = ["status", "flag", "note"]


def open_database(file=None):
    file = file or os.environ.get("CAAP_DB") or str(DEFAULT_DB_PATH)
    if file != ":memory:":
        Path(file).parent.mkdir(parents=True, exist_ok=True)
    db = open_connection(file)
    db.execute("PRAGMA journal_mode = WAL")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS patients (
          id              TEXT PRIMARY KEY,
          name            TEXT NOT NULL,
          display_id      TEXT NOT NULL,
          stage           TEXT NOT NULL DEFAULT '',
          gestation       TEXT NOT NULL DEFAULT '',
          blood_pressure  TEXT NOT NULL DEFAULT '',
          weight          TEXT NOT NULL DEFAULT '',
          update_text     TEXT NOT NULL DEFAULT '',
          appointment     TEXT NOT NULL DEFAULT '',
          status          TEXT NOT NULL DEFAULT '',
          flag            TEXT NOT NULL DEFAULT '',
          note            TEXT NOT NULL DEFAULT '',
          history         TEXT NOT NULL DEFAULT '',
          updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    seed_if_empty(db)
    users.setup_users(db)
    users.setup_password_resets(db)
    sessions.setup_sessions(db)
    audit.setup_audit(db)
    visits.setup_visits(db)
    caretaker.setup_caretaker(db)
    notes.setup_notes(db)
    return db


def seed_if_empty(db):
    if query_one(db, "SELECT COUNT(*) AS count FROM patients")["count"] > 0:
        return
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    columns = ["id"] + list(FIELD_COLUMNS.values())
    sql = f"INSERT INTO patients ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})"
    with transaction(db):
        for patient_id, patient in seed.items():
            values = [patient.get(field) or "" for field in FIELD_COLUMNS]
            db.execute(sql, [patient_id, *values])


def to_patient(row):
    patient = {"id": row["id"]}
    for field, column in FIELD_COLUMNS.items():
        patient[field] = row[column]
    patient["updatedAt"] = row["updated_at"]
    return patient


def list_patients(db):
    return [to_patient(row) for row in query_all(db, "SELECT * FROM patients ORDER BY name")]


def get_patient(db, patient_id):
    row = query_one(db, "SELECT * FROM patients WHERE id = ?", (patient_id,))
    return to_patient(row) if row else None


def update_patient(db, patient_id, changes):
    assignments = []
    values = []
    for field in EDITABLE_FIELDS:
        if isinstance(changes.get(field), str):
            assignments.append(f"{FIELD_COLUMNS[field]} = ?")
            values.append(changes[field])
    if not assignments:
        return get_patient(db, patient_id)
    assignments.append("updated_at = datetime('now')")
    cursor = db.execute(
        f"UPDATE patients SET {', '.join(assignments)} WHERE id = ?", [*values, patient_id]
    )
    return get_patient(db, patient_id) if cursor.rowcount else None
