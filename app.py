"""
Comp Finder – Streamlit Application
====================================
Find near-identical comparable properties using HouseCanary data via Snowflake.
"""

import os
import requests
import streamlit as st
import pandas as pd

from datetime import date, timedelta

from snowflake_client import find_subject_property, find_candidate_comps
from comp_engine import score_and_rank
from geo_utils import haversine_miles
from auth import require_auth, get_user, logout

GOOGLE_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")

# ── Page config ───────────────────────────────────────────────────────────

st.set_page_config(page_title="Comp Finder", page_icon="🏠", layout="wide")
st.markdown('<style>input[type="number"]{color:#ffffff !important;} [data-testid="stSliderThumbValue"] p{color:#ffffff !important;} .st-emotion-cache-rnt0ih{background-color:#ffffff !important;}</style>', unsafe_allow_html=True)

# ── Authentication ────────────────────────────────────────────────────────
require_auth()

st.title("🏠 Comp Finder")
st.caption("Find near-identical comparable properties powered by MortgagePros")

# ── Sidebar controls ─────────────────────────────────────────────────────

PROPERTY_TYPES = ["SFR", "CONDO", "TOWNHOUSE", "MULTI_FAMILY", "MANUFACTURED", "MOBILE", "LAND", "AGRICULTURAL", "RENTAL_UNIT", "TIMESHARE"]

with st.sidebar:
    # ── User info + logout ────────────────────────────────────────────
    _user = get_user()
    if _user:
        _name = _user.get("name") or _user.get("username") or _user.get("email", "User")
        st.markdown(f"👤 **{_name}**")

        if st.button("Sign Out", use_container_width=True):
            logout()
            st.rerun()
        st.divider()

    st.header("Search Filters")

    # ── Distance & display ────────────────────────────────────────────────
    max_radius = st.number_input("Max radius (miles)", min_value=0.1, max_value=50.0, value=2.0, step=0.25, format="%.2f")
    min_similarity = st.slider("Min similarity score", 0, 100, 50, 5)
    max_comps = st.slider("Max comps to display", 1, 50, 20, 1)

    # ── Sale recency ──────────────────────────────────────────────────────
    st.subheader("Sale Recency")
    recency_mode = st.radio("Filter by", ["Months back", "Date range"], horizontal=True)
    if recency_mode == "Months back":
        months_back = st.number_input("Months", min_value=1, max_value=360, value=12, step=1)
        sale_start_date = None
        sale_end_date = None
    else:
        months_back = None
        rc1, rc2 = st.columns(2)
        with rc1:
            sale_start_date = st.date_input("From", value=date.today() - timedelta(days=365))
        with rc2:
            sale_end_date = st.date_input("To", value=date.today())

    # ── Property filters ──────────────────────────────────────────────────
    st.subheader("Property Filters")

    filter_prop_type = st.multiselect("Property Type", PROPERTY_TYPES, default=[], placeholder="All")

    st.markdown("**Bedrooms**")
    fp1, fp2 = st.columns(2)
    with fp1:
        filter_beds_min = st.number_input("Min Beds", min_value=0, max_value=20, value=0, step=1)
    with fp2:
        filter_beds_max = st.number_input("Max Beds", min_value=0, max_value=20, value=0, step=1, help="0 = no max")

    st.markdown("**Bathrooms**")
    fb1, fb2 = st.columns(2)
    with fb1:
        filter_baths_min = st.number_input("Min Baths", min_value=0.0, max_value=20.0, value=0.0, step=0.5)
    with fb2:
        filter_baths_max = st.number_input("Max Baths", min_value=0.0, max_value=20.0, value=0.0, step=0.5, help="0 = no max")

    st.markdown("**Square Footage**")
    fs1, fs2 = st.columns(2)
    with fs1:
        filter_sqft_min = st.number_input("Min SqFt", min_value=0, max_value=100000, value=0, step=100)
    with fs2:
        filter_sqft_max = st.number_input("Max SqFt", min_value=0, max_value=100000, value=0, step=100, help="0 = no max")

    st.markdown("**Lot Size (sqft)**")
    fl1, fl2 = st.columns(2)
    with fl1:
        filter_lot_min = st.number_input("Min Lot", min_value=0, max_value=10000000, value=0, step=500)
    with fl2:
        filter_lot_max = st.number_input("Max Lot", min_value=0, max_value=10000000, value=0, step=500, help="0 = no max")

    st.markdown("**Year Built**")
    fy1, fy2 = st.columns(2)
    with fy1:
        filter_year_min = st.number_input("From Year", min_value=1800, max_value=2030, value=1800, step=1)
    with fy2:
        filter_year_max = st.number_input("To Year", min_value=1800, max_value=2030, value=2030, step=1)

    st.markdown("**Stories**")
    fst1, fst2 = st.columns(2)
    with fst1:
        filter_stories_min = st.number_input("Min Stories", min_value=0.0, max_value=10.0, value=0.0, step=0.5)
    with fst2:
        filter_stories_max = st.number_input("Max Stories", min_value=0.0, max_value=10.0, value=0.0, step=0.5, help="0 = no max")

    st.markdown("**Sold Price**")
    _price_presets = ["0"] + [f"{v:,}" for v in range(150000, 2000001, 50000)]
    fpr1, fpr2 = st.columns(2)
    with fpr1:
        _pm = st.selectbox("Min Price", _price_presets, index=0)
        filter_price_min = int(_pm.replace(",", ""))
    with fpr2:
        _px = st.selectbox("Max Price", _price_presets, index=0, help="0 = no max")
        filter_price_max = int(_px.replace(",", ""))

    # ── Yes/No filters ────────────────────────────────────────────────────
    st.subheader("Features")
    filter_basement = st.selectbox("Basement", ["Any", "Yes", "No"])
    filter_pool = st.selectbox("Pool", ["Any", "Yes", "No"])
    filter_garage = st.selectbox("Garage", ["Any", "Yes", "No"])
    filter_hoa = st.selectbox("HOA", ["Any", "Yes", "No"])

# Collect all filters into a dict for the query
advanced_filters = {
    "prop_types": filter_prop_type if filter_prop_type else None,
    "beds_min": filter_beds_min if filter_beds_min > 0 else None,
    "beds_max": filter_beds_max if filter_beds_max > 0 else None,
    "baths_min": filter_baths_min if filter_baths_min > 0 else None,
    "baths_max": filter_baths_max if filter_baths_max > 0 else None,
    "sqft_min": filter_sqft_min if filter_sqft_min > 0 else None,
    "sqft_max": filter_sqft_max if filter_sqft_max > 0 else None,
    "lot_min": filter_lot_min if filter_lot_min > 0 else None,
    "lot_max": filter_lot_max if filter_lot_max > 0 else None,
    "year_min": filter_year_min if filter_year_min > 1800 else None,
    "year_max": filter_year_max if filter_year_max < 2030 else None,
    "stories_min": filter_stories_min if filter_stories_min > 0 else None,
    "stories_max": filter_stories_max if filter_stories_max > 0 else None,
    "price_min": filter_price_min if filter_price_min > 0 else None,
    "price_max": filter_price_max if filter_price_max > 0 else None,
    "basement": filter_basement,
    "pool": filter_pool,
    "garage": filter_garage,
    "hoa": filter_hoa,
}

# ── Google Places helpers ─────────────────────────────────────────────────

@st.cache_data(ttl=300)
def google_autocomplete(query: str) -> list[dict]:
    """Call Google Places Autocomplete API and return suggestions."""
    if not query or len(query) < 3 or not GOOGLE_API_KEY:
        return []
    resp = requests.get(
        "https://maps.googleapis.com/maps/api/place/autocomplete/json",
        params={
            "input": query,
            "types": "address",
            "components": "country:us",
            "key": GOOGLE_API_KEY,
        },
        timeout=5,
    )
    data = resp.json()
    return [
        {"description": p["description"], "place_id": p["place_id"]}
        for p in data.get("predictions", [])
    ]


@st.cache_data(ttl=3600)
def google_place_details(place_id: str) -> dict:
    """Get structured address from a Google Place ID."""
    resp = requests.get(
        "https://maps.googleapis.com/maps/api/place/details/json",
        params={
            "place_id": place_id,
            "fields": "address_components",
            "key": GOOGLE_API_KEY,
        },
        timeout=5,
    )
    result = resp.json().get("result", {})
    comps = result.get("address_components", [])
    parts = {}
    for c in comps:
        t = c["types"][0] if c["types"] else ""
        if t == "street_number":
            parts["street_number"] = c["long_name"]
        elif t == "route":
            parts["route"] = c["short_name"]
        elif t == "postal_code":
            parts["zipcode"] = c["long_name"]
    street = f"{parts.get('street_number', '')} {parts.get('route', '')}".strip()
    return {"street": street, "zipcode": parts.get("zipcode", "")}


# ── Address input ─────────────────────────────────────────────────────────

use_autocomplete = bool(GOOGLE_API_KEY and GOOGLE_API_KEY != "YOUR_GOOGLE_MAPS_API_KEY")

if use_autocomplete:
    st.markdown("##### 📍 Search Address")
    query = st.text_input("Start typing an address…", key="addr_query", placeholder="e.g. 123 Main St, Springfield")
    suggestions = google_autocomplete(query)

    matched = None
    if suggestions:
        options = ["Select Address From List"] + [s["description"] for s in suggestions]
        choice = st.selectbox("Select address", options, key="addr_select", label_visibility="collapsed")
        if choice != "Select Address From List":
            matched = next((s for s in suggestions if s["description"] == choice), None)
    if matched:
            details = google_place_details(matched["place_id"])
            if details["street"] and details["zipcode"]:
                st.session_state["_resolved_street"] = details["street"]
                st.session_state["_resolved_zip"] = details["zipcode"]

    # Manual override / fallback
    with st.expander("Or enter address manually"):
        col1, col2 = st.columns([3, 1])
        with col1:
            manual_street = st.text_input("Street Address", placeholder="123 Main St", key="manual_street")
        with col2:
            manual_zip = st.text_input("ZIP Code", placeholder="90210", key="manual_zip")

    # Determine final address: prefer Google-resolved, fall back to manual
    street_address = st.session_state.get("_resolved_street", "") or manual_street
    zip_code = st.session_state.get("_resolved_zip", "") or manual_zip
else:
    st.markdown("##### 📍 Search Address")
    col1, col2 = st.columns([3, 1])
    with col1:
        street_address = st.text_input("Street Address", placeholder="123 Main St")
    with col2:
        zip_code = st.text_input("ZIP Code", placeholder="90210")

search_clicked = st.button("🔍 Find Comps", type="primary", use_container_width=True)

# ── Main logic ────────────────────────────────────────────────────────────

if search_clicked:
    if not street_address or not zip_code:
        st.warning("Please enter both a street address and ZIP code.")
        st.stop()

    # ── Look up subject property ──────────────────────────────────────────
    with st.spinner("Looking up subject property…"):
        subject_df = find_subject_property(street_address, zip_code)

    if subject_df.empty:
        st.error(
            "Subject property not found in the database. "
            "Check the address/ZIP or verify your Snowflake config."
        )
        st.stop()

    subject = subject_df.iloc[0].to_dict()

    if subject.get("latitude") is None or subject.get("longitude") is None:
        st.error("Subject property has no latitude/longitude in the database.")
        st.stop()

    # ── Find comps ────────────────────────────────────────────────────────
    with st.spinner("Searching for comparable properties…"):
        raw_comps = find_candidate_comps(
            address=street_address,
            zipcode=zip_code,
            subject_lat=subject["latitude"],
            subject_lon=subject["longitude"],
            max_radius=max_radius,
            months_back=months_back,
            sale_start_date=str(sale_start_date) if sale_start_date else None,
            sale_end_date=str(sale_end_date) if sale_end_date else None,
            filters=advanced_filters,
            limit=200,
        )

    if raw_comps.empty:
        st.info(
            "No comps found. Try increasing the radius or sale recency window."
        )
        st.stop()

    comps = score_and_rank(
        subject=subject,
        comps_df=raw_comps,
        min_similarity=min_similarity,
        max_comps=max_comps,
        search_radius=max_radius,
    )

    if comps.empty:
        st.info(
            "No comps met the minimum similarity threshold. Try lowering it."
        )
        st.stop()

    # Cache results in session state
    st.session_state["_results_subject"] = subject
    st.session_state["_results_comps"] = comps

# ── Display results (from session state) ──────────────────────────────────

if "_results_subject" not in st.session_state:
    st.stop()

subject = st.session_state["_results_subject"]
comps = st.session_state["_results_comps"]

# ── Subject property card ─────────────────────────────────────────────
st.subheader("Subject Property")
sc1, sc2, sc3, sc4 = st.columns(4)
sc1.metric("Address", f"{subject['address']}, {subject['city']}, {subject['state']} {subject['zipcode']}")
sc2.metric("Lat / Lon", f"{subject['latitude']:.6f}, {subject['longitude']:.6f}")

mc1, mc2, mc3, mc4, mc5, mc6 = st.columns(6)
mc1.metric("Beds", subject.get("bedrooms", "N/A"))
mc2.metric("Baths", subject.get("bathrooms", "N/A"))
mc3.metric("SqFt", f"{subject.get('sqft', 'N/A'):,}" if subject.get("sqft") else "N/A")
mc4.metric("Lot Size", f"{subject.get('lot_size', 'N/A'):,}" if subject.get("lot_size") else "N/A")
mc5.metric("Year Built", subject.get("year_built", "N/A"))
mc6.metric("AVM Value", f"${subject.get('avm_value', 0):,.0f}" if subject.get("avm_value") else "N/A")

st.caption(f"Property type: **{subject.get('property_type', 'Unknown')}**")
st.divider()

st.subheader(f"Comparable Properties ({len(comps)} found)")

# ── Street View thumbnail URLs ────────────────────────────────────────
def _street_view_url(lat, lon, w=120, h=80):
    return (
        f"https://maps.googleapis.com/maps/api/streetview"
        f"?size={w}x{h}&location={lat},{lon}&fov=90&pitch=10&key={GOOGLE_API_KEY}"
    ) if pd.notna(lat) and pd.notna(lon) and GOOGLE_API_KEY else None

comps["photo"] = comps.apply(lambda r: _street_view_url(r["latitude"], r["longitude"], 600, 400), axis=1)

# ── Data table ────────────────────────────────────────────────────────
display_cols = [
    "address", "city", "zipcode",
    "distance_miles", "bedrooms", "bathrooms", "sqft", "lot_size",
    "year_built", "stories", "property_type",
    "sale_date", "sale_price", "price_per_sqft", "similarity", "match_tier",
]
# Only show columns that exist
display_cols = [c for c in display_cols if c in comps.columns]

import json as _json
import streamlit.components.v1 as _components

# Column display headers
_col_labels = {
    "address": "Address", "city": "City", "zipcode": "ZIP",
    "distance_miles": "Dist (mi)", "bedrooms": "Beds", "bathrooms": "Baths",
    "sqft": "SqFt", "lot_size": "Lot", "year_built": "Yr Built",
    "stories": "Stories", "property_type": "Type",
    "sale_date": "Sale Date", "sale_price": "Sale Price",
    "price_per_sqft": "$/SqFt", "similarity": "Sim %", "match_tier": "Match",
}

# Format cell values
def _fmt(col, val):
    if pd.isna(val):
        return "N/A"
    if col == "sale_price":
        return f"${val:,.0f}"
    if col == "price_per_sqft":
        return f"${val:,.0f}"
    if col == "distance_miles":
        return f"{val:.2f}"
    if col == "similarity":
        return f"{val:.0f}"
    if col == "stories":
        return f"{val:.1f}"
    if col in ("sqft", "lot_size"):
        return f"{val:,.0f}" if isinstance(val, (int, float)) else str(val)
    return str(val)

_tier_bg = {"Green": "#166534", "Yellow": "#a16207", "Red": "#991b1b"}

# Build table rows as JSON
_rows = []
for _, r in comps.iterrows():
    photo = r.get("photo", "") or ""
    tier = r.get("match_tier", "")
    cells = [_fmt(c, r.get(c)) for c in display_cols]
    _rows.append({"photo": photo, "tier": tier, "cells": cells})

_rows_json = _json.dumps(_rows)
_headers_json = _json.dumps(["Photo"] + [_col_labels.get(c, c) for c in display_cols])

_table_html = (
    '<style>'
    '*{box-sizing:border-box;margin:0;padding:0;}'
    '.tw{width:100%;overflow-x:auto;font-family:-apple-system,BlinkMacSystemFont,sans-serif;font-size:13px;}'
    'table{border-collapse:collapse;width:100%;min-width:1200px;}'
    'th{position:sticky;top:0;background:#0f172a;color:#e2e8f0;padding:8px 10px;text-align:left;font-weight:600;border-bottom:2px solid #334155;white-space:nowrap;z-index:2;}'
    'td{padding:6px 10px;border-bottom:1px solid #1e293b;white-space:nowrap;vertical-align:middle;}'
    'tr{transition:filter .15s;}tr:hover{filter:brightness(1.2);}'
    '.thumb{width:80px;height:54px;object-fit:cover;border-radius:4px;cursor:pointer;transition:transform .2s;}'
    '.thumb:hover{transform:scale(1.08);}'
    '.lb{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.88);display:none;align-items:center;justify-content:center;z-index:9999;cursor:pointer;flex-direction:column;}'
    '.lb img{max-width:90%;max-height:80%;border-radius:8px;box-shadow:0 0 40px rgba(0,0,0,.5);}'
    '.lb .info{color:#f1f5f9;margin-top:12px;font-size:14px;background:rgba(30,41,59,.85);padding:8px 20px;border-radius:6px;}'
    '</style>'
    '<div class="tw"><table><thead><tr id="hdr"></tr></thead><tbody id="tbody"></tbody></table></div>'
    '<div class="lb" id="lb" onclick="this.style.display=\'none\'"><img id="lbI"><div class="info" id="lbT"></div></div>'
    '<script>'
    'const rows=' + _rows_json + ';'
    'const hdrs=' + _headers_json + ';'
    'const tierBg={"Green":"#166534","Yellow":"#a16207","Red":"#991b1b"};'
    'const hdr=document.getElementById("hdr");'
    'hdrs.forEach(h=>{const th=document.createElement("th");th.textContent=h;hdr.appendChild(th);});'
    'const tbody=document.getElementById("tbody");'
    'rows.forEach(r=>{'
    '  const tr=document.createElement("tr");'
    '  const bg=tierBg[r.tier]||"transparent";'
    '  tr.style.cssText="background:"+bg+";color:#fff;";'
    '  const td0=document.createElement("td");'
    '  if(r.photo){'
    '    const img=document.createElement("img");img.className="thumb";img.src=r.photo;'
    '    img.onclick=()=>{document.getElementById("lbI").src=r.photo;document.getElementById("lbT").textContent=r.cells[0]+" \\u00B7 "+r.cells.slice(-3).join(" \\u00B7 ");document.getElementById("lb").style.display="flex";};'
    '    td0.appendChild(img);'
    '  } else {td0.textContent="—";}'
    '  tr.appendChild(td0);'
    '  r.cells.forEach(v=>{const td=document.createElement("td");td.textContent=v;tr.appendChild(td);});'
    '  tbody.appendChild(tr);'
    '});'
    '</script>'
)

_num_rows = len(comps)
_table_height = min(70 + _num_rows * 68, 800)
_components.html(_table_html, height=_table_height, scrolling=True)

# Tier legend
st.markdown(
    "🟢 **Green** = Strong match (≥80%, no red fields)  ·  "
    "🟡 **Yellow** = Moderate match (50–79%)  ·  "
    "🔴 **Red** = Weak match (<50%)",
)

# ── Map view (precise pins) ───────────────────────────────────────────
st.subheader("Map")
map_style_choice = st.radio("Map Style", ["Satellite", "Road"], horizontal=True, index=0)
_map_type_ids = {"Satellite": "hybrid", "Road": "roadmap"}
map_type_id = _map_type_ids[map_style_choice]

# Build comp map data with tooltip info
comp_map_data = comps.copy()
comp_map_data = comp_map_data.rename(columns={"latitude": "lat", "longitude": "lon"})
comp_map_data["sale_price_fmt"] = comp_map_data["sale_price"].apply(lambda x: f"${x:,.0f}" if pd.notna(x) else "N/A")
comp_map_data["similarity_fmt"] = comp_map_data["similarity"].apply(lambda x: f"{x:.0f}")

# Map pin colors by match tier
pin_colors = {"Green": [34, 197, 94, 220], "Yellow": [234, 179, 8, 220], "Red": [239, 68, 68, 220]}
comp_map_data["pin_color"] = comp_map_data["match_tier"].map(pin_colors).apply(lambda x: x if x is not None else [0, 100, 255, 200])

# ── Google Maps Satellite via HTML component ─────────────────────────

_addr = subject.get("address", "")
_subject_data = [{
    "lat": subject["latitude"],
    "lon": subject["longitude"],
    "address": "SUBJECT: " + _addr,
    "sale_price_fmt": chr(8212),
    "similarity_fmt": chr(8212),
    "match_tier": chr(8212),
    "photo_url": _street_view_url(subject["latitude"], subject["longitude"], 200, 120),
}]

comp_map_data["photo_url"] = comp_map_data.apply(lambda r: _street_view_url(r["lat"], r["lon"], 200, 120), axis=1)
_comp_data = comp_map_data[["lat", "lon", "address", "sale_price_fmt", "similarity_fmt", "match_tier", "pin_color", "photo_url"]].to_dict(orient="records")

_subject_json = _json.dumps(_subject_data)
_comp_json = _json.dumps(_comp_data)
_center_lat = subject["latitude"]
_center_lng = subject["longitude"]

_html = (
    '<!DOCTYPE html>'
    '<html><head>'
    '<script src="https://unpkg.com/deck.gl@latest/dist.min.js"></script>'
    '<script async src="https://maps.googleapis.com/maps/api/js?key=' + GOOGLE_API_KEY + '&loading=async&callback=initMap"></script>'
    '<style>html,body,#map{margin:0;padding:0;width:100%;height:600px;}</style>'
    '</head><body><div id="map"></div><script>'
    'const subjectData = ' + _subject_json + ';'
    'const compData = ' + _comp_json + ';'
    'function initMap() {'
    '  const deckOverlay = new deck.GoogleMapsOverlay({'
    '    layers: ['
    '      new deck.ScatterplotLayer({'
    '        id: "subject", data: subjectData,'
    '        getPosition: d => [d.lon, d.lat],'
    '        getFillColor: [6, 66, 127, 220],'
    '        getLineColor: [255, 255, 255, 255],'
    '        getRadius: 30, radiusUnits: "meters", radiusMinPixels: 6, radiusMaxPixels: 30, lineWidthMinPixels: 2, stroked: true, pickable: true,'
    '      }),'
    '      new deck.ScatterplotLayer({'
    '        id: "comps", data: compData,'
    '        getPosition: d => [d.lon, d.lat],'
    '        getFillColor: d => d.pin_color || [0, 100, 255, 200],'
    '        getLineColor: [255, 255, 255, 180],'
    '        getRadius: 20, radiusUnits: "meters", radiusMinPixels: 4, radiusMaxPixels: 24, lineWidthMinPixels: 1, stroked: true, pickable: true,'
    '      }),'
    '    ],'
    '  });'
    '  const map = new google.maps.Map(document.getElementById("map"), {'
    '    center: {lat: ' + str(_center_lat) + ', lng: ' + str(_center_lng) + '},'
    "    zoom: 16, mapTypeId: '" + map_type_id + "', tilt: 0,"
    '  });'
    '  deckOverlay.setMap(map);'
    '  const tooltip = document.createElement("div");'
    '  tooltip.style.cssText = "display:none;position:fixed;pointer-events:none;z-index:9999;background:#1e293b;color:#f1f5f9;border-radius:8px;font-size:13px;padding:8px;max-width:220px;box-shadow:0 4px 12px rgba(0,0,0,.4);";'
    '  document.body.appendChild(tooltip);'
    '  deckOverlay.setProps({'
    '    onHover: (info) => {'
    '      if (info.object) {'
    '        const d = info.object;'
    '        const img = d.photo_url ? \'<img src="\' + d.photo_url + \'" style="width:200px;height:120px;object-fit:cover;border-radius:4px;margin-bottom:6px;">\' : "";'
    '        tooltip.innerHTML = img + \'<div style="font-weight:600;margin-bottom:4px;">\' + (d.address||"") + \'</div><div>Price: \' + (d.sale_price_fmt||"\\u2014") + \'</div><div>Similarity: \' + (d.similarity_fmt||"\\u2014") + \'%</div><div>Match: \' + (d.match_tier||"\\u2014") + \'</div>\';'
    '        tooltip.style.display = "block";'
    '        tooltip.style.left = (info.x + 12) + "px";'
    '        tooltip.style.top = (info.y + 12) + "px";'
    '      } else { tooltip.style.display = "none"; }'
    '    }'
    '  });'
    '}'
    '</script></body></html>'
)

_components.html(_html, height=620)

# ── Summary statistics ────────────────────────────────────────────────
st.subheader("Summary Statistics")
s1, s2, s3, s4, s5 = st.columns(5)
s1.metric("Avg Sale Price", f"${comps['sale_price'].mean():,.0f}")
s2.metric("Median Sale Price", f"${comps['sale_price'].median():,.0f}")
if "price_per_sqft" in comps.columns and comps["price_per_sqft"].notna().any():
    s3.metric("Avg $/SqFt", f"${comps['price_per_sqft'].mean():,.0f}")
    s4.metric("Median $/SqFt", f"${comps['price_per_sqft'].median():,.0f}")
s5.metric("Avg Distance", f"{comps['distance_miles'].mean():.2f} mi")

s6, s7, _ = st.columns(3)
s6.metric("Avg Similarity", f"{comps['similarity'].mean():.1f}")
s7.metric("Comps Found", len(comps))

# ── CSV export ────────────────────────────────────────────────────────
st.divider()
csv = comps[display_cols].to_csv(index=False)
st.download_button(
    label="📥 Download Comps as CSV",
    data=csv,
    file_name="comp_results.csv",
    mime="text/csv",
    use_container_width=True,
)
