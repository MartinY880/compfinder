"""
db.py — Simple Postgres persistence layer.
Tables:
  - rov_reports: Claude's JSON output per ROV generation
  - comp_searches: comp search history per user
"""

import json
import os
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL", "")


@contextmanager
def _get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist. Called once at app startup."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS rov_reports (
                    id              SERIAL PRIMARY KEY,
                    user_email      TEXT,
                    subject_address TEXT,
                    comps_count     INT,
                    input_payload   JSONB,
                    agent_json      JSONB,
                    created_at      TIMESTAMP DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS comp_searches (
                    id              SERIAL PRIMARY KEY,
                    user_email      TEXT,
                    subject_address TEXT,
                    filters         JSONB,
                    result_count    INT,
                    created_at      TIMESTAMP DEFAULT NOW()
                );

                -- Add input_payload column if it doesn't exist yet
                ALTER TABLE rov_reports ADD COLUMN IF NOT EXISTS input_payload JSONB;
            """)


# ── Write helpers ─────────────────────────────────────────────────────────────

def log_rov_report(user_email: str, subject_address: str, comps_count: int, agent_json: dict, input_payload: dict | None = None):
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO rov_reports (user_email, subject_address, comps_count, input_payload, agent_json) "
                "VALUES (%s, %s, %s, %s, %s)",
                (user_email, subject_address, comps_count, json.dumps(input_payload) if input_payload else None, json.dumps(agent_json)),
            )


def log_comp_search(user_email: str, subject_address: str, filters: dict, result_count: int):
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO comp_searches (user_email, subject_address, filters, result_count) "
                "VALUES (%s, %s, %s, %s)",
                (user_email, subject_address, json.dumps(filters), result_count),
            )


# ── Read helpers ──────────────────────────────────────────────────────────────

def get_rov_reports(limit: int = 200) -> list[dict]:
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, user_email, subject_address, comps_count, input_payload, agent_json, created_at "
                "FROM rov_reports ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


def get_comp_searches(limit: int = 200) -> list[dict]:
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, user_email, subject_address, filters, result_count, created_at "
                "FROM comp_searches ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


def find_recent_search(subject_address: str, days: int = 7) -> dict | None:
    """Return the most recent ROV report for this address within `days`, or None."""
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT user_email, subject_address, comps_count, agent_json, created_at "
                "FROM rov_reports "
                "WHERE LOWER(subject_address) = LOWER(%s) "
                "  AND created_at >= NOW() - INTERVAL '%s days' "
                "ORDER BY created_at DESC LIMIT 1",
                (subject_address, days),
            )
            row = cur.fetchone()
            return dict(row) if row else None
