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

                -- Add revision columns if they don't exist yet
                ALTER TABLE rov_reports ADD COLUMN IF NOT EXISTS revision_notes TEXT;
                ALTER TABLE rov_reports ADD COLUMN IF NOT EXISTS revised_agent_json JSONB;
                ALTER TABLE rov_reports ADD COLUMN IF NOT EXISTS parent_id INT REFERENCES rov_reports(id);

                -- Add enrichment_stats column if it doesn't exist yet
                ALTER TABLE comp_searches ADD COLUMN IF NOT EXISTS enrichment_stats JSONB;

                CREATE TABLE IF NOT EXISTS escalations (
                    id              SERIAL PRIMARY KEY,
                    address         TEXT,
                    loan_number     TEXT,
                    borrower_name   TEXT,
                    generated_at    TIMESTAMP DEFAULT NOW(),
                    output_pdf_path TEXT
                );

                ALTER TABLE escalations ADD COLUMN IF NOT EXISTS agent_json JSONB;
                ALTER TABLE escalations ADD COLUMN IF NOT EXISTS input_payload JSONB;
            """)


# ── Write helpers ─────────────────────────────────────────────────────────────

def log_rov_report(user_email: str, subject_address: str, comps_count: int, agent_json: dict, input_payload: dict | None = None) -> int:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO rov_reports (user_email, subject_address, comps_count, input_payload, agent_json) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (user_email, subject_address, comps_count, json.dumps(input_payload) if input_payload else None, json.dumps(agent_json)),
            )
            return cur.fetchone()[0]


def log_rov_revision(parent_id: int, user_email: str, subject_address: str, comps_count: int,
                     revision_notes: str, revised_agent_json: dict, input_payload: dict | None = None) -> int:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO rov_reports (user_email, subject_address, comps_count, input_payload, "
                "agent_json, revision_notes, revised_agent_json, parent_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (user_email, subject_address, comps_count,
                 json.dumps(input_payload) if input_payload else None,
                 json.dumps(revised_agent_json),
                 revision_notes, json.dumps(revised_agent_json), parent_id),
            )
            return cur.fetchone()[0]


def log_comp_search(
    user_email: str,
    subject_address: str,
    filters: dict,
    result_count: int,
    enrichment_summary: dict | None = None,
) -> int | None:
    """Insert a comp search record. Returns the inserted row ID."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO comp_searches (user_email, subject_address, filters, result_count, enrichment_stats) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (
                    user_email,
                    subject_address,
                    json.dumps(filters),
                    result_count,
                    json.dumps(enrichment_summary) if enrichment_summary else None,
                ),
            )
            row = cur.fetchone()
            return row[0] if row else None


def log_escalation(
    address: str,
    loan_number: str,
    borrower_name: str,
    output_pdf_path: str | None = None,
    agent_json: dict | None = None,
    input_payload: dict | None = None,
) -> int:
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO escalations (address, loan_number, borrower_name, output_pdf_path, agent_json, input_payload) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                (address, loan_number, borrower_name, output_pdf_path,
                 json.dumps(agent_json) if agent_json else None,
                 json.dumps(input_payload) if input_payload else None),
            )
            return cur.fetchone()[0]


def get_escalations(limit: int = 200) -> list[dict]:
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, address, loan_number, borrower_name, generated_at, output_pdf_path, agent_json, input_payload "
                "FROM escalations ORDER BY generated_at DESC LIMIT %s",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


def update_enrichment_stats(search_id: int, enrichment_summary: dict):
    """Update the enrichment_stats JSONB for an existing search record."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE comp_searches SET enrichment_stats = %s WHERE id = %s",
                (json.dumps(enrichment_summary), search_id),
            )


# ── Read helpers ──────────────────────────────────────────────────────────────

def get_rov_reports(limit: int = 200) -> list[dict]:
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, user_email, subject_address, comps_count, input_payload, agent_json, "
                "revision_notes, revised_agent_json, parent_id, created_at "
                "FROM rov_reports ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


def get_comp_searches(limit: int = 200) -> list[dict]:
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, user_email, subject_address, filters, result_count, enrichment_stats, created_at "
                "FROM comp_searches ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


def find_recent_escalation(address: str, days: int = 60) -> dict | None:
    """Return the most recent escalation for this address within `days`, or None."""
    with _get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, address, loan_number, borrower_name, generated_at, agent_json "
                "FROM escalations "
                "WHERE LOWER(address) = LOWER(%s) "
                "  AND generated_at >= NOW() - INTERVAL '%s days' "
                "ORDER BY generated_at DESC LIMIT 1",
                (address, days),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def find_recent_search(subject_address: str, days: int = 60) -> dict | None:
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
