# Comp Finder — Additions Todo

## Phase 1: Comp Match Accuracy (Scoring & Color Coding)

This is the highest priority — it refines the core value of the tool and builds on existing infrastructure.

- [ ] **1.1 Refactor `comp_engine.py` scoring to use tiered accuracy bands**
  - Return per-field tier (Green/Yellow/Red) alongside the overall score
  - **Bedrooms**: Exact = Green, ±1 = Yellow, ±2 = Red
  - **Bathrooms**: Exact = Green, ±1 = Yellow, ±2 = Red
  - **Square Footage (GLA)**: 1–10% diff = Green, 11–20% = Yellow, 21%+ = Red
  - **Year Built**: 1–10% diff = Green, 11–20% = Yellow, 21%+ = Red
  - **Distance**: Within 50% of search radius = Green, beyond 50% = Yellow

- [ ] **1.2 Add a composite accuracy tier per comp**
  - Calculate an overall match tier from the individual field tiers
  - 100% match across all fields = Green
  - 80–99% = Yellow
  - 50–79% = Red

- [ ] **1.3 Color-code the results table rows**
  - Apply Green / Yellow / Red row highlighting in the `st.dataframe` display
  - Add a "Match Tier" column (Green / Yellow / Red)

- [ ] **1.4 Color-code map pins by match tier**
  - Green pins for Green-tier comps
  - Yellow pins for Yellow-tier
  - Red pins for Red-tier
  - Subject property pin stays distinct (brand blue)

---

## Phase 2: Min–Max Sold Price Filter

Quick win — adds a missing filter that users expect.

- [ ] **2.1 Add Sold Price filter inputs to the sidebar**
  - Min Sold Price and Max Sold Price number inputs (0 = no limit)
  - Place in the sidebar under "Property Filters" section

- [ ] **2.2 Pass price filter to `snowflake_client.py` query**
  - Add `price_min` / `price_max` to the `advanced_filters` dict
  - Add `WHERE` clauses in `find_candidate_comps()` SQL

---

## Phase 3: Satellite / Terrain Map View

Improves the visual experience with actual terrain imagery.

- [ ] **3.1 Switch pydeck map style to satellite-streets**
  - Change `map_style` from `streets-v12` to `mapbox://styles/mapbox/satellite-streets-v12`
  - This gives real aerial/terrain imagery with street labels overlaid

- [ ] **3.2 Add map style toggle**
  - Add a radio/selectbox above the map: "Streets" / "Satellite" / "Terrain"
  - `satellite-streets-v12` for Satellite, `outdoors-v12` for Terrain, `streets-v12` for Streets

---

## Phase 4: Property Photos (If Possible)

Stretch goal — depends on data availability in Snowflake / external APIs.

- [ ] **4.1 Investigate photo availability**
  - Check if HouseCanary data in Snowflake includes image URLs (run `schema_discovery.py`)
  - If not, research Google Street View Static API or Zillow/Redfin image APIs

- [ ] **4.2 Display property thumbnail in results**
  - If image URLs exist: add an "Image" column to the results table with `st.column_config.ImageColumn`
  - If using Street View API: construct URL from lat/lon and render inline

- [ ] **4.3 Add photo to map tooltip**
  - Include a small thumbnail in the pydeck tooltip HTML (if supported)
  - Fallback: show photo in an expander below the map when a comp is selected
