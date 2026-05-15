import os

# ---------------------------------------------------------------------------
# Snowflake connection settings – sourced from environment variables
# ---------------------------------------------------------------------------
SNOWFLAKE_CONNECTION = {
    "account": os.getenv("SNOWFLAKE_ACCOUNT", ""),
    "user": os.getenv("SNOWFLAKE_USER", ""),
    "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE", ""),
    "database": os.getenv("SNOWFLAKE_DATABASE", ""),
    "schema": os.getenv("SNOWFLAKE_SCHEMA", "PUBLIC"),
    "role": os.getenv("SNOWFLAKE_ROLE", ""),
}

SNOWFLAKE_PRIVATE_KEY_PATH = os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH", "certs/rsa_key.p8")

# ---------------------------------------------------------------------------
# Table & column mapping – UPDATE THESE after running schema_discovery.py
# ---------------------------------------------------------------------------
# The keys on the left are internal names the app uses.
# The values on the right must match your actual Snowflake table/column names.

TABLES = {
    "properties": "{db}.{schema}.BULK_PROPERTY_DATA_PRIVATE_SHARE_USA",
}

COLUMNS = {
    # Address / geo
    "address": "ADDRESS",
    "city": "CITY",
    "state": "STATE",
    "zipcode": "ZIPCODE",
    "latitude": "LATITUDE",
    "longitude": "LONGITUDE",
    # Property characteristics
    "bedrooms": "BEDROOMS",
    "bathrooms": "BATHROOMS_TOTAL_PROJECTED",
    "sqft": "LIVING_AREA",
    "above_grade_sqft": "LIVING_AREA_ABOVE_GRADE",
    "lot_size": "LOT_SIZE",
    "year_built": "YEAR_BUILT",
    "property_type": "PROPERTY_TYPE",
    "stories": "STORIES_NUMBER",
    "basement_yn": "BASEMENT_YN",
    "pool_yn": "POOL_YN",
    "garage": "PARKING_GARAGE",
    "association_yn": "ASSOCIATION_YN",
    "association_name": "ASSOCIATION1_NAME",
    # Sales (last recorded sale)
    "sale_date": "LAST_CLOSE_DATE",
    "sale_price": "LAST_CLOSE_PRICE",
    # Valuation
    "avm_value": "HC_VALUE_ESTIMATE",
}

# ---------------------------------------------------------------------------
# Helpers – resolve fully-qualified table names at runtime
# ---------------------------------------------------------------------------

def get_table(key: str) -> str:
    """Return the fully-qualified table name with db/schema substituted."""
    db = SNOWFLAKE_CONNECTION["database"]
    schema = SNOWFLAKE_CONNECTION["schema"]
    return TABLES[key].format(db=db, schema=schema)


def col(key: str) -> str:
    """Return the real column name for an internal key."""
    return COLUMNS[key]
