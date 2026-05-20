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

from snowflake_client import find_subject_property, find_candidate_comps, find_property_by_address
from comp_engine import score_and_rank
from geo_utils import haversine_miles
from auth import require_auth, get_user, logout, get_logout_url
from auth import _delete_cookie as _auth_delete_cookie
from auth import _set_logout_flag_cookie, _redirect_top, has_scope, has_role
from generate_rov import generate_rov_pdf
from db import init_db, log_comp_search, log_rov_report, find_recent_search

init_db()

GOOGLE_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")

# ── Page config ───────────────────────────────────────────────────────────

st.set_page_config(page_title="Comp Finder", page_icon="🏠", layout="wide")
# Hide Streamlit's auto-generated page navigation so admin link only appears for authorized users
st.markdown('<style>[data-testid="stSidebarNav"] {display: none;} input[type="number"]{color:#ffffff !important;} [data-testid="stSliderThumbValue"] p{color:#ffffff !important;} .st-emotion-cache-rnt0ih{background-color:#ffffff !important;}</style>', unsafe_allow_html=True)

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

        if has_scope("manage:compfinder") or has_role("manage:compfinder"):
            st.page_link("pages/admin.py", label="🔧 Admin Panel", use_container_width=True)

        if st.button("Sign Out", key="_logout_btn", use_container_width=True):
            _logout_url = get_logout_url()
            # Clear server-side session state first.
            for _k in list(st.session_state.keys()):
                del st.session_state[_k]
            # Set both cookies AND navigate in a single parent-document script
            # so the cookies are guaranteed to be written before navigation.
            import json as _json_logout
            _safe_url = _json_logout.dumps(_logout_url)
            st.html(
                "<script>"
                "document.cookie='compfinder_logged_out=1;path=/;max-age=60;SameSite=Lax';"
                "document.cookie='compfinder_auth=;path=/;max-age=0;SameSite=Lax';"
                f"window.location.href={_safe_url};"
                "</script>"
            )
            st.stop()
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
    fpr1, fpr2 = st.columns(2)
    with fpr1:
        filter_price_min = st.number_input("Min Price", min_value=0, max_value=10_000_000, value=0, step=50_000, help="0 = no min. Type any value or use arrows for $50K increments.")
    with fpr2:
        filter_price_max = st.number_input("Max Price", min_value=0, max_value=10_000_000, value=0, step=50_000, help="0 = no max. Type any value or use arrows for $50K increments.")

    # ── Yes/No filters ────────────────────────────────────────────────────
    st.subheader("Features")
    filter_basement = st.selectbox("Basement", ["Any", "Yes", "No"])
    filter_pool = st.selectbox("Pool", ["Any", "Yes", "No"])
    filter_garage = st.selectbox("Garage", ["Any", "Yes", "No"])
    filter_hoa = st.selectbox("HOA", ["Any", "Yes", "No"])

    # Placeholder for reload button (rendered after fingerprint check)
    _reload_placeholder = st.empty()

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

# ── Filter change detection: show reload button when filters change ────────
_filter_fingerprint = (
    max_radius, min_similarity, max_comps,
    months_back, str(sale_start_date), str(sale_end_date),
    tuple(sorted((advanced_filters or {}).items())),
)
_prev_fingerprint = st.session_state.get("_filter_fingerprint")
_filters_changed = (
    _prev_fingerprint is not None
    and _prev_fingerprint != _filter_fingerprint
    and "_results_subject" in st.session_state
)
_reload_clicked = False
if _filters_changed:
    with _reload_placeholder:
        _reload_clicked = st.button("🔄 Reload with new filters", type="primary", use_container_width=True)

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

# ── Duplicate search warning ──────────────────────────────────────────────
if st.session_state.get("_dup_pending") and not st.session_state.get("_dup_confirmed"):
    _info = st.session_state.get("_dup_info", {})
    st.warning(
        f"⚠️ An ROV was already generated for this address on **{_info.get('when', 'recently')}** "
        f"by **{_info.get('who', 'a user')}** ({_info.get('count', 0)} comps used). "
        f"Are you sure you want to search again?"
    )
    _, _dup_c1, _dup_c2, _ = st.columns([2, 1, 1, 2])
    with _dup_c1:
        if st.button("Yes, search again", key="_dup_yes", use_container_width=True):
            st.session_state["_dup_confirmed"] = True
            st.session_state.pop("_dup_pending", None)
            st.rerun()
    with _dup_c2:
        _stored_json = _info.get("agent_json")
        if _stored_json:
            # Rebuild the PDF from the stored agent JSON
            import tempfile
            from pdf_builder import build_rov_pdf
            try:
                with tempfile.TemporaryDirectory() as _tmpdir:
                    import os
                    _rov_path = os.path.join(_tmpdir, "filled_rov.pdf")
                    build_rov_pdf("Main_ROV_blank.pdf", _stored_json, _rov_path)
                    with open(_rov_path, "rb") as _f:
                        _rov_bytes = _f.read()
                _addr_label = _info.get("address", "property").replace(" ", "_")
                st.download_button(
                    label="📄 Download ROV",
                    data=_rov_bytes,
                    file_name=f"ROV_{_addr_label}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    key="_dup_download",
                )
            except Exception:
                st.button("📄 Download ROV", key="_dup_dl_err", use_container_width=True, disabled=True, help="Could not rebuild PDF from stored data.")
        else:
            st.button("📄 Download ROV", key="_dup_dl_na", use_container_width=True, disabled=True, help="No stored ROV data available.")
    st.stop()

# ── Main logic ────────────────────────────────────────────────────────────

if search_clicked or st.session_state.get("_dup_confirmed") or _reload_clicked:
    # For filter-change re-runs, use the stored address from the last search
    if _reload_clicked and not search_clicked:
        street_address = st.session_state.get("_last_search_street", street_address)
        zip_code = st.session_state.get("_last_search_zip", zip_code)

    if not street_address or not zip_code:
        st.warning("Please enter both a street address and ZIP code.")
        st.stop()

    # ── Check for recent ROV generation (skip on filter-only changes) ──
    if not _reload_clicked:
        _search_addr = street_address
        try:
            _recent = find_recent_search(_search_addr, days=7)
        except Exception:
            _recent = None  # don't block on DB issues

        if _recent and not st.session_state.get("_dup_confirmed"):
            _when = _recent["created_at"].strftime("%b %d, %Y at %I:%M %p")
            _who = _recent.get("user_email", "someone")
            st.session_state["_dup_pending"] = True
            st.session_state["_dup_info"] = {
                "when": _when,
                "who": _who,
                "count": _recent.get("comps_count", 0),
                "agent_json": _recent.get("agent_json"),
                "address": _recent.get("subject_address", "property"),
            }
            st.rerun()
        else:
            st.session_state.pop("_dup_confirmed", None)
            st.session_state.pop("_dup_pending", None)
            st.session_state.pop("_dup_info", None)

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
    st.session_state["_filter_fingerprint"] = _filter_fingerprint
    st.session_state["_last_search_street"] = street_address
    st.session_state["_last_search_zip"] = zip_code
    # Clear manual comps on new search/filter change
    st.session_state["_manual_comps"] = []

    # Log the search (only on explicit searches, not filter reloads)
    if not _reload_clicked:
        try:
            _user = get_user()
            log_comp_search(
                user_email=(_user or {}).get("email", "unknown"),
                subject_address=f"{street_address}, {zip_code}",
                filters=advanced_filters,
                result_count=len(comps),
            )
        except Exception:
            pass  # never block the UI for logging failures

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

# ── Add Additional Comps by Address ───────────────────────────────────
st.subheader("➕ Add Comparable by Address")
st.caption("Look up a property not in the search results and add it to the comps table.")

if "_manual_comps" not in st.session_state:
    st.session_state["_manual_comps"] = []

# Google Places autocomplete for manual comp lookup (same UX as main search)
if use_autocomplete:
    _mc_query = st.text_input("Start typing an address…", key="_mc_addr_query", placeholder="e.g. 456 Oak Ave, Springfield", on_change=lambda: None)
    # Prevent enter from triggering — only process when a dropdown selection is made
    _mc_suggestions = google_autocomplete(_mc_query) if _mc_query and len(_mc_query) >= 3 else []

    _mc_matched = None
    if _mc_suggestions:
        _mc_options = ["Select Address From List"] + [s["description"] for s in _mc_suggestions]
        _mc_choice = st.selectbox("Select address", _mc_options, key="_mc_addr_select", label_visibility="collapsed")
        if _mc_choice != "Select Address From List":
            _mc_matched = next((s for s in _mc_suggestions if s["description"] == _mc_choice), None)

    if _mc_matched:
        _mc_details = google_place_details(_mc_matched["place_id"])
        if _mc_details["street"] and _mc_details["zipcode"]:
            _manual_addr = _mc_details["street"]
            _manual_zip = _mc_details["zipcode"]
            # Check if already added (silently skip — no message needed after rerun)
            _already_manual = any(
                _mc.get("address", "").upper() == _manual_addr.upper()
                for _mc in st.session_state["_manual_comps"]
            )
            _already_in_comps = _manual_addr.upper() in comps["address"].str.upper().values
            if not _already_manual and not _already_in_comps:
                with st.spinner("Looking up property…"):
                    _found = find_property_by_address(
                        _manual_addr.strip(),
                        _manual_zip.strip(),
                        float(subject["latitude"]),
                        float(subject["longitude"]),
                    )
                if _found.empty:
                    st.warning(f"Property not found: {_manual_addr}, {_manual_zip}")
                else:
                    st.session_state["_manual_comps"].append(_found.iloc[0].to_dict())
                    st.rerun()
else:
    _mc1, _mc2, _mc3 = st.columns([3, 2, 1])
    with _mc1:
        _manual_addr = st.text_input("Address", placeholder="e.g. 456 Oak Ave", key="_manual_comp_addr")
    with _mc2:
        _manual_zip = st.text_input("ZIP Code", placeholder="e.g. 36093", key="_manual_comp_zip")
    with _mc3:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        _add_comp = st.button("🔍 Look Up", key="_add_manual_comp", use_container_width=True)

    if _add_comp and _manual_addr.strip() and _manual_zip.strip():
        with st.spinner("Looking up property…"):
            _found = find_property_by_address(
                _manual_addr.strip(),
                _manual_zip.strip(),
                float(subject["latitude"]),
                float(subject["longitude"]),
            )
        if _found.empty:
            st.warning(f"Property not found: {_manual_addr.strip()}, {_manual_zip.strip()}")
        else:
            _row = _found.iloc[0]
            _dup = any(
                _mc.get("address", "").upper() == str(_row.get("address", "")).upper()
                for _mc in st.session_state["_manual_comps"]
            )
            if not _dup and str(_row.get("address", "")).upper() in comps["address"].str.upper().values:
                _dup = True
            if _dup:
                st.info("This property is already in the results.")
            else:
                st.session_state["_manual_comps"].append(_row.to_dict())
                st.rerun()

# Merge manual comps into the main comps dataframe
if st.session_state.get("_manual_comps"):
    _manual_df = pd.DataFrame(st.session_state["_manual_comps"])
    for _needed_col in ["price_per_sqft", "similarity", "match_tier", "apn_mls", "data_source"]:
        if _needed_col not in _manual_df.columns:
            if _needed_col == "price_per_sqft":
                _manual_df[_needed_col] = _manual_df.apply(
                    lambda r: r["sale_price"] / r["sqft"] if pd.notna(r.get("sale_price")) and pd.notna(r.get("sqft")) and r.get("sqft", 0) > 0 else 0, axis=1)
            elif _needed_col == "similarity":
                _manual_df[_needed_col] = 0
            elif _needed_col == "match_tier":
                _manual_df[_needed_col] = "Manual"
            elif _needed_col == "data_source":
                _manual_df[_needed_col] = "Manual"
            else:
                _manual_df[_needed_col] = ""
    comps = pd.concat([comps, _manual_df], ignore_index=True)

    # Show added comps
    for _mi, _mc in enumerate(st.session_state["_manual_comps"]):
        _mc_price = f"${_mc.get('sale_price', 0):,.0f}" if _mc.get("sale_price") else "N/A"
        st.caption(f"✓ Added: **{_mc.get('address', 'N/A')}**, {_mc.get('city', '')} — {_mc_price}")

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
    "distance_miles", "bedrooms", "bathrooms", "sqft", "above_grade_sqft", "lot_size",
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
    "sqft": "SqFt", "above_grade_sqft": "Above Grade", "lot_size": "Lot", "year_built": "Yr Built",
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
_num_manual = len(st.session_state.get("_manual_comps", []))

_table_html = (
    '<style>'
    '*{box-sizing:border-box;margin:0;padding:0;}'
    '.tw{width:100%;overflow-x:auto;font-family:-apple-system,BlinkMacSystemFont,sans-serif;font-size:13px;}'
    'table{border-collapse:collapse;width:100%;min-width:1200px;}'
    'th{position:sticky;top:0;background:#0f172a;color:#e2e8f0;padding:8px 10px;text-align:left;font-weight:600;border-bottom:2px solid #334155;white-space:nowrap;z-index:2;}'
    'td{padding:6px 10px;border-bottom:1px solid #1e293b;white-space:nowrap;vertical-align:middle;}'
    'tr{transition:filter .15s;}tr:hover{filter:brightness(1.2);}'
    'tr.selected{outline:2px solid #38bdf8;outline-offset:-2px;}'
    'tr.hide-unselected{display:none;}'
    '.thumb{width:80px;height:54px;object-fit:cover;border-radius:4px;cursor:pointer;transition:transform .2s;}'
    '.thumb:hover{transform:scale(1.08);}'
    '.lb{position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.88);display:none;align-items:center;justify-content:center;z-index:9999;cursor:pointer;flex-direction:column;}'
    '.lb img{max-width:90%;max-height:80%;border-radius:8px;box-shadow:0 0 40px rgba(0,0,0,.5);}'
    '.lb .info{color:#f1f5f9;margin-top:12px;font-size:14px;background:rgba(30,41,59,.85);padding:8px 20px;border-radius:6px;}'
    '.toolbar{display:flex;align-items:center;gap:10px;padding:8px 12px;background:#1e293b;border-bottom:1px solid #334155;}'
    '.toolbar .sel-count{font-size:13px;color:#94a3b8;}'
    '.toolbar .sel-count b{color:#38bdf8;}'
    '.toolbar button{background:#334155;border:1px solid #475569;color:#e2e8f0;padding:5px 14px;border-radius:6px;cursor:pointer;font-size:12px;transition:all .15s;}'
    '.toolbar button:hover{background:#475569;}'
    '.toolbar button.active{background:#38bdf8;color:#0f172a;border-color:#38bdf8;}'
    '.sel-chk{width:16px;height:16px;accent-color:#38bdf8;cursor:pointer;}'
    '</style>'
    '<div class="toolbar">'
    '  <span class="sel-count"><b id="selCnt">0</b> selected</span>'
    '  <button id="btnShowSel" onclick="toggleShowSelected()">Show Selected Only</button>'
    '  <button onclick="clearSelection()">Clear Selection</button>'
    '</div>'
    '<div class="tw"><table><thead><tr id="hdr"></tr></thead><tbody id="tbody"></tbody></table></div>'
    '<div class="lb" id="lb" onclick="this.style.display=\'none\'"><img id="lbI"><div class="info" id="lbT"></div></div>'
    '<script>'
    'const rows=' + _rows_json + ';'
    'const hdrs=' + _headers_json + ';'
    'const numManual=' + str(_num_manual) + ';'
    'const tierBg={"Green":"#166534","Yellow":"#a16207","Red":"#991b1b"};'
    'let showSelOnly=false;'
    'const hdr=document.getElementById("hdr");'
    'const thSel=document.createElement("th");thSel.textContent="✔";thSel.style.width="36px";thSel.style.textAlign="center";hdr.appendChild(thSel);'
    'hdrs.forEach(h=>{const th=document.createElement("th");th.textContent=h;hdr.appendChild(th);});'
    'const tbody=document.getElementById("tbody");'
    'const allTrs=[];'
    'rows.forEach((r,i)=>{'
    '  const tr=document.createElement("tr");'
    '  const bg=tierBg[r.tier]||"transparent";'
    '  tr.style.cssText="background:"+bg+";color:#fff;";'
    '  tr.dataset.idx=i;'
    '  const tdSel=document.createElement("td");tdSel.style.textAlign="center";'
    '  const chk=document.createElement("input");chk.type="checkbox";chk.className="sel-chk";'
    '  chk.onchange=()=>{tr.classList.toggle("selected",chk.checked);updateCount();applyFilter();};'
    '  tdSel.appendChild(chk);tr.appendChild(tdSel);'
    '  const td0=document.createElement("td");'
    '  if(r.photo){'
    '    const img=document.createElement("img");img.className="thumb";img.src=r.photo;'
    '    img.onclick=()=>{document.getElementById("lbI").src=r.photo;document.getElementById("lbT").textContent=r.cells[0]+" \\u00B7 "+r.cells.slice(-3).join(" \\u00B7 ");document.getElementById("lb").style.display="flex";};'
    '    td0.appendChild(img);'
    '  } else {td0.textContent="—";}'
    '  tr.appendChild(td0);'
    '  r.cells.forEach(v=>{const td=document.createElement("td");td.textContent=v;tr.appendChild(td);});'
    '  tbody.appendChild(tr);'
    '  allTrs.push(tr);'
    '});\n'
    'if(numManual>0){\n'
    '  var startIdx=rows.length-numManual;\n'
    '  for(var i=startIdx;i<rows.length;i++){\n'
    '    var chk=allTrs[i].querySelector(".sel-chk");\n'
    '    if(chk&&!chk.checked){chk.checked=true;allTrs[i].classList.add("selected");}\n'
    '  }\n'
    '  allTrs[startIdx].scrollIntoView({behavior:"smooth",block:"center"});\n'
    '}\n'
    'function updateCount(){document.getElementById("selCnt").textContent=document.querySelectorAll(".sel-chk:checked").length;}\n'
    'updateCount();\n'
    'function applyFilter(){'
    '  if(!showSelOnly){allTrs.forEach(t=>t.classList.remove("hide-unselected"));return;}'
    '  allTrs.forEach(t=>{'
    '    const chk=t.querySelector(".sel-chk");'
    '    t.classList.toggle("hide-unselected",!chk.checked);'
    '  });'
    '}'
    'function toggleShowSelected(){'
    '  showSelOnly=!showSelOnly;'
    '  document.getElementById("btnShowSel").classList.toggle("active",showSelOnly);'
    '  document.getElementById("btnShowSel").textContent=showSelOnly?"Show All":"Show Selected Only";'
    '  applyFilter();'
    '}'
    'function clearSelection(){'
    '  document.querySelectorAll(".sel-chk").forEach(c=>{c.checked=false;});'
    '  allTrs.forEach(t=>t.classList.remove("selected"));'
    '  showSelOnly=false;'
    '  document.getElementById("btnShowSel").classList.remove("active");'
    '  document.getElementById("btnShowSel").textContent="Show Selected Only";'
    '  updateCount();applyFilter();'
    '}'
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
    '    },'
    '    onClick: (info) => {'
    '      if (info.layer && info.layer.id === "comps" && info.object) {'
    '        const idx = info.index;'
    '        try {'
    '          const iframes = window.parent.document.querySelectorAll("iframe");'
    '          for (const iframe of iframes) {'
    '            try {'
    '              const chks = iframe.contentDocument.querySelectorAll(".sel-chk");'
    '              if (chks.length > 0 && idx < chks.length) {'
    '                if (!chks[idx].checked) { chks[idx].checked = true; chks[idx].dispatchEvent(new Event("change")); }'
    '                const row = chks[idx].closest("tr");'
    '                if (row) { row.classList.add("selected"); row.scrollIntoView({behavior:"smooth",block:"center"}); }'
    '                const cnt = iframe.contentDocument.getElementById("selCnt");'
    '                if (cnt) cnt.textContent = iframe.contentDocument.querySelectorAll(".sel-chk:checked").length;'
    '                break;'
    '              }'
    '            } catch(e) {}'
    '          }'
    '        } catch(e) {}'
    '      }'
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

# ── PDF Appraisal Rebuttal Export ─────────────────────────────────────
st.divider()
st.subheader("📄 Export Appraisal Rebuttal PDF")
st.caption("Select comparable properties and generate a professional PDF report to dispute an appraisal value.")

# Build address labels for the multiselect
_addr_labels = []
for _i, _r in comps.iterrows():
    _price = f"${_r['sale_price']:,.0f}" if pd.notna(_r.get("sale_price")) else "N/A"
    _sim = f"{_r['similarity']:.0f}%" if pd.notna(_r.get("similarity")) else ""
    _addr_labels.append(f"{_r.get('address', 'N/A')}, {_r.get('city', '')} — {_price} ({_sim} match)")

selected_labels = st.multiselect(
    "Select comps to include in the PDF",
    options=_addr_labels,
    default=None,
    placeholder="Choose one or more comparable properties…",
)

if selected_labels:
    selected_indices = [_addr_labels.index(lbl) for lbl in selected_labels]
    selected_comps = comps.iloc[selected_indices].copy()

    # ── ROV-specific inputs ───────────────────────────────────────────
    client_name = st.text_input(
        "Borrower Name",
        placeholder="e.g. John Smith",
        help="Enter the borrower/client name.",
    )
    loan_number = st.text_input(
        "Loan Number",
        placeholder="e.g. 1234567890",
        help="Enter the loan/application number.",
    )

    appraisal_file = st.file_uploader(
        "Upload appraisal PDF",
        type="pdf",
        help="Claude will extract the subject property address from this PDF.",
    )

    # ── Visual page selector (shown after PDF upload) ─────────────────
    selected_pages = None
    if appraisal_file is not None:
        import fitz  # PyMuPDF
        import base64 as _b64

        # Cache rendered thumbnails in session state keyed by file name+size
        # Only render low-res thumbnails upfront; hi-res rendered on demand per click
        _file_key = f"pdf_thumbs_{appraisal_file.name}_{appraisal_file.size}"
        if _file_key != st.session_state.get("_pdf_thumb_key"):
            with st.spinner("Rendering PDF pages…"):
                _doc = fitz.open(stream=appraisal_file.getvalue(), filetype="pdf")
                _thumbs = []
                for _pg in _doc:
                    _pix_lo = _pg.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), colorspace=fitz.csRGB, alpha=False)
                    _thumbs.append(_pix_lo.tobytes("png"))
                _doc.close()
            # Clear any stale checkbox keys from a previous upload
            for _k in list(st.session_state.keys()):
                if _k.startswith("_pgchk_") or _k.startswith("_pdf_hires_"):
                    del st.session_state[_k]
            st.session_state["_pdf_thumb_key"] = _file_key
            st.session_state["_pdf_thumbs"] = _thumbs
            st.session_state["_pdf_raw_bytes"] = appraisal_file.getvalue()
            st.session_state["_pdf_sel_pages"] = {1}  # auto-select page 1

        _thumbs = st.session_state["_pdf_thumbs"]
        _total_pages = len(_thumbs)
        _current_sel = st.session_state.get("_pdf_sel_pages", {1})

        _sel_count = len(_current_sel)
        _sel_label = ", ".join(str(p) for p in sorted(_current_sel)) if _current_sel else "none"

        st.markdown(
            f"**📄 {_total_pages} pages** — click a page to preview. "
            f"<b>{_sel_count} selected</b> ({_sel_label})",
            unsafe_allow_html=True,
        )

        st.markdown("""
        <style>
        .pdf-thumb { border-radius: 6px; border: 3px solid transparent; width: 100%; cursor: pointer; }
        .pdf-thumb.selected { border-color: #4CAF50; }
        </style>
        """, unsafe_allow_html=True)

        # Dialog popup for hi-res page preview
        @st.dialog("Page Preview", width="large")
        def _show_page_preview(pg_num: int):
            st.markdown(f"**Page {pg_num}**")
            _hires_key = f"_pdf_hires_{pg_num}"
            if _hires_key not in st.session_state:
                _doc = fitz.open(stream=st.session_state["_pdf_raw_bytes"], filetype="pdf")
                _hpg = _doc[pg_num - 1]
                _hpix = _hpg.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), colorspace=fitz.csRGB, alpha=False)
                st.session_state[_hires_key] = _hpix.tobytes("png")
                _doc.close()
            st.image(st.session_state[_hires_key], use_container_width=True)

        COLS_PER_ROW = 5
        with st.container(height=600):
            for _row_start in range(0, _total_pages, COLS_PER_ROW):
                _row_end = min(_row_start + COLS_PER_ROW, _total_pages)
                _cols = st.columns(COLS_PER_ROW)
                for _ci, _pg_idx in enumerate(range(_row_start, _row_end)):
                    _pg_num = _pg_idx + 1
                    _is_sel = _pg_num in _current_sel
                    with _cols[_ci]:
                        if st.button(
                            f"Page {_pg_num}",
                            key=f"_pgview_{_pg_num}",
                            use_container_width=True,
                            type="tertiary",
                        ):
                            _show_page_preview(_pg_num)
                        _img_b64 = _b64.b64encode(_thumbs[_pg_idx]).decode()
                        _sel_class = "selected" if _is_sel else ""
                        st.markdown(
                            f'<img class="pdf-thumb {_sel_class}" '
                            f'src="data:image/png;base64,{_img_b64}" />',
                            unsafe_allow_html=True,
                        )
                        _checked = st.checkbox(
                            "Include",
                            value=_is_sel,
                            key=f"_pgchk_{_pg_num}",
                        )
                        if _checked:
                            _current_sel.add(_pg_num)
                        else:
                            _current_sel.discard(_pg_num)

        st.session_state["_pdf_sel_pages"] = _current_sel
        selected_pages = sorted(_current_sel) if _current_sel else None

        if not selected_pages:
            st.warning("Please select at least one page.")

    _rov_ready = appraisal_file and selected_pages and client_name.strip() and loan_number.strip()
    generate_clicked = st.button(
        "Generate ROV",
        type="primary",
        use_container_width=True,
        disabled=not _rov_ready,
    )

    if generate_clicked:
        import tempfile

        # Build ROV comp payload
        rov_comps = []
        for _, r in selected_comps.iterrows():
            rov_comps.append({
                "address":        str(r.get("address", "")),
                "city":           str(r.get("city", "")),
                "state":          str(r.get("state", "")),
                "zipcode":        str(r.get("zipcode", "")),
                "distance_miles": float(r["distance_miles"]) if pd.notna(r.get("distance_miles")) else 0,
                "bedrooms":       int(r["bedrooms"]) if pd.notna(r.get("bedrooms")) else 0,
                "bathrooms":      float(r["bathrooms"]) if pd.notna(r.get("bathrooms")) else 0,
                "sqft":           float(r["sqft"]) if pd.notna(r.get("sqft")) else 0,
                "above_grade_sqft": float(r["above_grade_sqft"]) if pd.notna(r.get("above_grade_sqft")) else 0,
                "lot_size":       float(r["lot_size"]) if pd.notna(r.get("lot_size")) else 0,
                "year_built":     int(r["year_built"]) if pd.notna(r.get("year_built")) else 0,
                "stories":        int(r["stories"]) if pd.notna(r.get("stories")) else 0,
                "property_type":  str(r.get("property_type", "")),
                "sale_date":      str(r.get("sale_date", "")),
                "sale_price":     float(r["sale_price"]) if pd.notna(r.get("sale_price")) else 0,
                "price_per_sqft": float(r["price_per_sqft"]) if pd.notna(r.get("price_per_sqft")) else 0,
                "similarity":     float(r["similarity"]) if pd.notna(r.get("similarity")) else 0,
                "apn_mls":        str(r.get("apn_mls", "")),
                "data_source":    str(r.get("data_source", "MLS")),
            })

        payload = {
            "date_submitted": date.today().strftime("%m/%d/%Y"),
            "client_name": client_name.strip() if client_name else "",
            "loan_number": loan_number.strip() if loan_number else "",
            "summary_reasoning": "",
            "auto_generate_summary": True,
            "subject_property": {
                "address": str(subject.get("address", "")),
                "city": str(subject.get("city", "")),
                "state": str(subject.get("state", "")),
                "zipcode": str(subject.get("zipcode", "")),
                "bedrooms": int(subject["bedrooms"]) if pd.notna(subject.get("bedrooms")) else 0,
                "bathrooms": float(subject["bathrooms"]) if pd.notna(subject.get("bathrooms")) else 0,
                "sqft": float(subject["sqft"]) if pd.notna(subject.get("sqft")) else 0,
                "above_grade_sqft": float(subject["above_grade_sqft"]) if pd.notna(subject.get("above_grade_sqft")) else 0,
                "lot_size": float(subject["lot_size"]) if pd.notna(subject.get("lot_size")) else 0,
                "year_built": int(subject["year_built"]) if pd.notna(subject.get("year_built")) else 0,
                "stories": int(subject["stories"]) if pd.notna(subject.get("stories")) else 0,
                "property_type": str(subject.get("property_type", "")),
            },
            "selected_comps": rov_comps,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            appraisal_path = os.path.join(tmpdir, "appraisal.pdf")

            # Write only the selected pages to the temp PDF
            from pypdf import PdfReader as _PdfR, PdfWriter as _PdfW
            import io as _io
            _src = _PdfR(_io.BytesIO(appraisal_file.getvalue()))
            _writer = _PdfW()
            for _pg_num in selected_pages:
                _writer.add_page(_src.pages[_pg_num - 1])  # 1-indexed → 0-indexed
            with open(appraisal_path, "wb") as f:
                _writer.write(f)

            output_path = os.path.join(tmpdir, "filled_rov.pdf")

            try:
                with st.spinner("Generating ROV Form... This may take up to a minute."):
                    result = generate_rov_pdf(
                        payload=payload,
                        appraisal_pdf_path=appraisal_path,
                        blank_form_path="Main_ROV_blank.pdf",
                        output_path=output_path,
                    )

                with open(output_path, "rb") as f:
                    pdf_bytes = f.read()

                _subj_addr = subject.get("address", "property").replace(" ", "_")
                st.success(f"✅ ROV generated with {len(selected_comps)} comparable properties")
                st.download_button(
                    label="📄 Download Filled ROV PDF",
                    data=pdf_bytes,
                    file_name=f"ROV_{_subj_addr}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    type="primary",
                )

                # Log the ROV report
                try:
                    _rov_user = get_user()
                    log_rov_report(
                        user_email=(_rov_user or {}).get("email", "unknown"),
                        subject_address=subject.get("address", ""),
                        comps_count=len(selected_comps),
                        agent_json=result["agent_output"],
                        input_payload=payload,
                    )
                except Exception:
                    pass  # never block the UI for logging failures

            except Exception as e:
                st.error(f"Failed to generate ROV: {e}")
                with st.expander("Error details"):
                    st.exception(e)
else:
    st.info("Select at least one comparable property above to generate the ROV.")