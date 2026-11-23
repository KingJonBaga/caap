"""Shared helpers: database access, timestamps and small input helpers."""
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
TIME_RE = re.compile(r"[0-9]{2}:[0-9]{2}")


def utc_now():
    return datetime.now(timezone.utc)


def sql_time(moment):
    """Format used in the database, comparable with SQLite's datetime('now')."""
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def iso_time(moment):
    """Format like JavaScript's toISOString(), e.g. 2026-11-24T09:40:00.000Z."""
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def text(value):
    """Turns a value from a request into trimmed text; missing values become ''."""
    return str(value).strip() if value else ""


def account_id(row_id):
    return "ACC-" + str(row_id).zfill(4)


def query_all(db, sql, params=()):
    return [dict(row) for row in db.execute(sql, params).fetchall()]


def query_one(db, sql, params=()):
    row = db.execute(sql, params).fetchone()
    return dict(row) if row else None


def open_connection(file):
    db = sqlite3.connect(file, isolation_level=None, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


@contextmanager
def transaction(db):
    db.execute("BEGIN")
    try:
        yield
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise


def table_columns(db, table):
    return [row["name"] for row in db.execute(f"PRAGMA table_info({table})").fetchall()]


def add_days(iso_date, days):
    from datetime import date

    return (date.fromisoformat(iso_date) + timedelta(days=days)).isoformat()
