"""
Snowflake Client
================
Connection management, subject property lookup, and candidate comps query.
All SQL uses config.py for table/column names — never hardcoded.
Single-table schema: BULK_PROPERTY_DATA_PRIVATE_SHARE_USA
"""

import streamlit as st
import snowflake.connector
import pandas as pd
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

from config import SNOWFLAKE_CONNECTION, SNOWFLAKE_PRIVATE_KEY_PATH, get_table, col
from geo_utils import normalize_address


def _load_private_key():
    """Load the RSA private key for Snowflake key pair auth."""
    with open(SNOWFLAKE_PRIVATE_KEY_PATH, "rb") as f:
        private_key = serialization.load_pem_private_key(
            f.read(), password=None, backend=default_backend()
        )
    return private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


# ── Connection ────────────────────────────────────────────────────────────

def _new_connection():
    """Create a fresh Snowflake connection."""
    params = {k: v for k, v in SNOWFLAKE_CONNECTION.items() if v}
    params["private_key"] = _load_private_key()
    return snowflake.connector.connect(**params)


def get_connection():
    """Return a working Snowflake connection, reconnecting if expired."""
    if "sf_conn" not in st.session_state or st.session_state.sf_conn is None:
        st.session_state.sf_conn = _new_connection()
    else:
        try:
            st.session_state.sf_conn.cursor().execute("SELECT 1")
        except Exception:
            try:
                st.session_state.sf_conn.close()
            except Exception:
                pass
            st.session_state.sf_conn = _new_connection()
    return st.session_state.sf_conn


# ── Subject Property Lookup ───────────────────────────────────────────────

@st.cache_data(ttl=3600)
def find_subject_property(address: str, zipcode: str) -> pd.DataFrame:
    """
    Look up the subject property by address + ZIP.
    Tries exact match first, then fuzzy LIKE match.
    Returns a single-row DataFrame or empty if not found.
    """
    conn = get_connection()
    table = get_table("properties")
    norm = normalize_address(address)

    select_cols = f"""
        {col('address')}        AS address,
        {col('city')}           AS city,
        {col('state')}          AS state,
        {col('zipcode')}        AS zipcode,
        {col('latitude')}       AS latitude,
        {col('longitude')}      AS longitude,
        {col('bedrooms')}       AS bedrooms,
        {col('bathrooms')}      AS bathrooms,
        {col('sqft')}           AS sqft,
        {col('lot_size')}       AS lot_size,
        {col('year_built')}     AS year_built,
        {col('property_type')}  AS property_type,
        {col('stories')}        AS stories,
        {col('basement_yn')}    AS basement_yn,
        {col('pool_yn')}        AS pool_yn,
        {col('garage')}         AS garage,
        {col('association_yn')}  AS association_yn,
        {col('association_name')} AS association_name,
        {col('avm_value')}      AS avm_value
    """

    # Try exact match
    sql_exact = f"""
    SELECT {select_cols}
    FROM {table}
    WHERE UPPER({col('address')}) = %(address)s
      AND {col('zipcode')} = %(zipcode)s
    LIMIT 1
    """
    df = pd.read_sql(sql_exact, conn, params={"address": norm, "zipcode": zipcode.strip()})
    df.columns = df.columns.str.lower()

    if df.empty:
        # Fuzzy fallback: LIKE match on the street number + partial name
        fuzzy = norm.replace(" ", "%")
        sql_fuzzy = f"""
        SELECT {select_cols}
        FROM {table}
        WHERE UPPER({col('address')}) LIKE %(fuzzy)s
          AND {col('zipcode')} = %(zipcode)s
        LIMIT 1
        """
        df = pd.read_sql(sql_fuzzy, conn, params={"fuzzy": f"%{fuzzy}%", "zipcode": zipcode.strip()})
        df.columns = df.columns.str.lower()

    return df


# ── Candidate Comps Query ─────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def find_candidate_comps(
    address: str,
    zipcode: str,
    subject_lat: float,
    subject_lon: float,
    max_radius: float = 2.0,
    months_back: int | None = 12,
    sale_start_date: str | None = None,
    sale_end_date: str | None = None,
    filters: dict | None = None,
    limit: int = 100,
) -> pd.DataFrame:
    """
    Find candidate comparable properties within *max_radius* miles.
    Filter by either months_back OR an explicit date range.
    Applies advanced property filters if provided.
    """
    conn = get_connection()
    table = get_table("properties")
    norm = normalize_address(address)
    filters = filters or {}

    # Build date filter clause
    if sale_start_date and sale_end_date:
        date_filter = f"AND TRY_TO_DATE({col('sale_date')}) BETWEEN %(sale_start)s AND %(sale_end)s"
    else:
        date_filter = f"AND TRY_TO_DATE({col('sale_date')}) >= DATEADD(month, -%(months_back)s, CURRENT_DATE())"

    # Build advanced filter clauses
    extra_clauses = []
    if filters.get("prop_types"):
        placeholders = ", ".join([f"%(pt_{i})s" for i in range(len(filters["prop_types"]))])
        extra_clauses.append(f"AND {col('property_type')} IN ({placeholders})")
    if filters.get("beds_min") is not None:
        extra_clauses.append(f"AND {col('bedrooms')} >= %(beds_min)s")
    if filters.get("beds_max") is not None:
        extra_clauses.append(f"AND {col('bedrooms')} <= %(beds_max)s")
    if filters.get("baths_min") is not None:
        extra_clauses.append(f"AND {col('bathrooms')} >= %(baths_min)s")
    if filters.get("baths_max") is not None:
        extra_clauses.append(f"AND {col('bathrooms')} <= %(baths_max)s")
    if filters.get("sqft_min") is not None:
        extra_clauses.append(f"AND {col('sqft')} >= %(sqft_min)s")
    if filters.get("sqft_max") is not None:
        extra_clauses.append(f"AND {col('sqft')} <= %(sqft_max)s")
    if filters.get("lot_min") is not None:
        extra_clauses.append(f"AND {col('lot_size')} >= %(lot_min)s")
    if filters.get("lot_max") is not None:
        extra_clauses.append(f"AND {col('lot_size')} <= %(lot_max)s")
    if filters.get("year_min") is not None:
        extra_clauses.append(f"AND {col('year_built')} >= %(year_min)s")
    if filters.get("year_max") is not None:
        extra_clauses.append(f"AND {col('year_built')} <= %(year_max)s")
    if filters.get("stories_min") is not None:
        extra_clauses.append(f"AND {col('stories')} >= %(stories_min)s")
    if filters.get("stories_max") is not None:
        extra_clauses.append(f"AND {col('stories')} <= %(stories_max)s")
    if filters.get("basement") == "Yes":
        extra_clauses.append(f"AND {col('basement_yn')} = 1")
    elif filters.get("basement") == "No":
        extra_clauses.append(f"AND ({col('basement_yn')} = 0 OR {col('basement_yn')} IS NULL)")
    if filters.get("pool") == "Yes":
        extra_clauses.append(f"AND {col('pool_yn')} = 1")
    elif filters.get("pool") == "No":
        extra_clauses.append(f"AND ({col('pool_yn')} = 0 OR {col('pool_yn')} IS NULL)")
    if filters.get("garage") == "Yes":
        extra_clauses.append(f"AND {col('garage')} IS NOT NULL AND {col('garage')} > 0")
    elif filters.get("garage") == "No":
        extra_clauses.append(f"AND ({col('garage')} IS NULL OR {col('garage')} = 0)")
    if filters.get("hoa") == "Yes":
        extra_clauses.append(f"AND {col('association_yn')} = 1")
    elif filters.get("hoa") == "No":
        extra_clauses.append(f"AND ({col('association_yn')} = 0 OR {col('association_yn')} IS NULL)")
    if filters.get("price_min") is not None:
        extra_clauses.append(f"AND {col('sale_price')} >= %(price_min)s")
    if filters.get("price_max") is not None:
        extra_clauses.append(f"AND {col('sale_price')} <= %(price_max)s")

    advanced_sql = "\n          ".join(extra_clauses)

    sql = f"""
    WITH candidates AS (
        SELECT
            {col('address')}        AS address,
            {col('city')}           AS city,
            {col('state')}          AS state,
            {col('zipcode')}        AS zipcode,
            {col('latitude')}       AS latitude,
            {col('longitude')}      AS longitude,
            {col('bedrooms')}       AS bedrooms,
            {col('bathrooms')}      AS bathrooms,
            {col('sqft')}           AS sqft,
            {col('lot_size')}       AS lot_size,
            {col('year_built')}     AS year_built,
            {col('property_type')}  AS property_type,
            {col('stories')}        AS stories,
            {col('basement_yn')}    AS basement_yn,
            {col('pool_yn')}        AS pool_yn,
            {col('garage')}         AS garage,
            {col('association_yn')}  AS association_yn,
            {col('association_name')} AS association_name,
            {col('sale_date')}      AS sale_date,
            {col('sale_price')}     AS sale_price,
            -- Haversine in SQL
            3958.8 * 2 * ASIN(SQRT(
                POWER(SIN(RADIANS({col('latitude')} - %(s_lat)s) / 2), 2) +
                COS(RADIANS(%(s_lat)s)) * COS(RADIANS({col('latitude')})) *
                POWER(SIN(RADIANS({col('longitude')} - %(s_lon)s) / 2), 2)
            )) AS distance_miles
        FROM {table}
        WHERE {col('latitude')}  BETWEEN %(s_lat)s - (%(max_radius)s / 69.0)
                                     AND %(s_lat)s + (%(max_radius)s / 69.0)
          AND {col('longitude')} BETWEEN %(s_lon)s - (%(max_radius)s / (69.0 * COS(RADIANS(%(s_lat)s))))
                                     AND %(s_lon)s + (%(max_radius)s / (69.0 * COS(RADIANS(%(s_lat)s))))
          AND NOT (UPPER({col('address')}) = %(norm_addr)s AND {col('zipcode')} = %(zipcode)s)
          {date_filter}
          AND {col('sale_price')} IS NOT NULL
          AND {col('sale_price')} > 0
          AND {col('latitude')} IS NOT NULL
          AND {col('longitude')} IS NOT NULL
          {advanced_sql}
    )
    SELECT * FROM candidates
    WHERE distance_miles <= %(max_radius)s
    ORDER BY distance_miles ASC
    LIMIT %(limit)s
    """

    params = {
        "s_lat": float(subject_lat),
        "s_lon": float(subject_lon),
        "max_radius": float(max_radius),
        "norm_addr": norm,
        "zipcode": zipcode.strip(),
        "months_back": int(months_back) if months_back else 12,
        "sale_start": sale_start_date,
        "sale_end": sale_end_date,
        "limit": int(limit),
    }

    # Add filter params
    if filters.get("prop_types"):
        for i, pt in enumerate(filters["prop_types"]):
            params[f"pt_{i}"] = pt
    for key in ["beds_min", "beds_max", "baths_min", "baths_max",
                 "sqft_min", "sqft_max", "lot_min", "lot_max",
                 "year_min", "year_max", "stories_min", "stories_max",
                 "price_min", "price_max"]:
        if filters.get(key) is not None:
            params[key] = filters[key]

    df = pd.read_sql(sql, conn, params=params)
    df.columns = df.columns.str.lower()
    return df
