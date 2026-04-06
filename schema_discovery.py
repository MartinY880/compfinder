#!/usr/bin/env python3
"""
Schema Discovery Utility
========================
Connect to Snowflake and list all available tables and columns in the
HouseCanary Data Share database.  Run this FIRST so you can populate
config.py with the correct table/column names.

Usage:
    python schema_discovery.py          # uses .env / environment variables
"""

import os
import sys

import snowflake.connector

from config import SNOWFLAKE_CONNECTION


def get_connection():
    params = {k: v for k, v in SNOWFLAKE_CONNECTION.items() if v}
    return snowflake.connector.connect(**params)


def discover():
    conn = get_connection()
    cur = conn.cursor()
    db = SNOWFLAKE_CONNECTION["database"]
    schema = SNOWFLAKE_CONNECTION["schema"]

    print(f"\n{'='*60}")
    print(f"  Snowflake Schema Discovery")
    print(f"  Database : {db}")
    print(f"  Schema   : {schema}")
    print(f"{'='*60}\n")

    # List tables
    cur.execute(
        "SELECT TABLE_NAME FROM {db}.INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME".format(db=db),
        (schema,),
    )
    tables = [row[0] for row in cur.fetchall()]

    if not tables:
        print("  No tables found. Check your database/schema settings.")
        conn.close()
        return

    print(f"  Found {len(tables)} table(s):\n")

    for table in tables:
        print(f"  ┌─ {table}")
        cur.execute(
            "SELECT COLUMN_NAME, DATA_TYPE "
            "FROM {db}.INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
            "ORDER BY ORDINAL_POSITION".format(db=db),
            (schema, table),
        )
        columns = cur.fetchall()
        for i, (col_name, col_type) in enumerate(columns):
            prefix = "  └──" if i == len(columns) - 1 else "  ├──"
            print(f"{prefix} {col_name:<40} {col_type}")
        print()

    conn.close()
    print("Done. Update config.py TABLES and COLUMNS with the names above.\n")


if __name__ == "__main__":
    try:
        discover()
    except snowflake.connector.errors.DatabaseError as exc:
        print(f"\nSnowflake connection error: {exc}", file=sys.stderr)
        sys.exit(1)
