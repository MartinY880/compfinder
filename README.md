# Comp Finder

Find near-identical comparable properties using HouseCanary data via Snowflake.

## Project Structure

```
comp-finder/
├── app.py                 # Streamlit UI
├── config.py              # Snowflake table/column mapping (edit this)
├── snowflake_client.py    # Snowflake connection and queries
├── comp_engine.py         # Similarity scoring and ranking
├── geo_utils.py           # Haversine formula and address normalization
├── schema_discovery.py    # Utility to explore Snowflake schema
├── requirements.txt       # Python dependencies
├── Dockerfile             # Container image
├── docker-compose.yml     # Compose stack (Portainer-ready)
├── .env.example           # Template for environment variables
└── README.md              # This file
```

## Setup

### 1. Configure Snowflake Credentials

```bash
cp .env.example .env
# Edit .env with your actual Snowflake credentials
```

### 2. Discover Your Schema

Before launching the app, you need to know the exact table and column names
in your HouseCanary Snowflake Data Share.

```bash
# Option A – run locally with Python
pip install snowflake-connector-python
python schema_discovery.py

# Option B – run inside Docker
docker compose run --rm comp-finder python schema_discovery.py
```

This prints every table and column in your database. Use the output to update
the `TABLES` and `COLUMNS` dictionaries in `config.py`.

### 3. Update config.py

Open `config.py` and set the correct table and column names based on
the schema discovery output. The internal keys on the left stay the same;
only update the values on the right.

### 4. Deploy

#### Docker Compose (local)

```bash
docker compose up -d --build
```

The app will be available at `http://localhost:8501`.

#### Portainer

1. In Portainer, go to **Stacks → Add Stack**.
2. Paste the contents of `docker-compose.yml`.
3. Add the environment variables from `.env` in the **Environment variables** section.
4. Deploy.

### 5. (Optional) Reverse Proxy

If you use Nginx Proxy Manager, create a proxy host pointing to
`comp-finder:8501` with WebSocket support enabled (Streamlit requires it).

## Usage

1. Enter a street address and ZIP code.
2. Adjust sidebar filters (radius, similarity, recency, max comps).
3. Click **Find Comps**.
4. Review the map, data table, and summary statistics.
5. Download results as CSV.

## Tech Stack

| Component | Technology |
|---|---|
| Frontend / UI | Streamlit |
| Language | Python 3.12 |
| Database | Snowflake (via snowflake-connector-python) |
| Data Source | HouseCanary via Snowflake Data Share |
| Mapping | pydeck |
| Deployment | Docker + Docker Compose / Portainer |
