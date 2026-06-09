"""
Comp Finder – Streamlit Application
====================================
Find near-identical comparable properties using HouseCanary data via Snowflake.
"""

import os
import tempfile
import requests
import streamlit as st
import pandas as pd

from datetime import date, timedelta

from snowflake_client import find_subject_property, find_candidate_comps, find_property_by_address
from comp_engine import score_and_rank
from geo_utils import haversine_miles
from auth import require_auth, get_user, logout, get_logout_url
import comp_enrichment
import propertyradar_client
from auth import _delete_cookie as _auth_delete_cookie
from auth import _set_logout_flag_cookie, _redirect_top, has_scope, has_role
from generate_rov import generate_rov_pdf
from db import init_db, log_comp_search, log_rov_report, log_rov_revision, find_recent_search, update_enrichment_stats, log_escalation, find_recent_escalation
import escalation_agent
import escalation_pdf_builder

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

# Clear all dup/escalation state when user starts a fresh search
if search_clicked:
    for _k in ["_dup_pending", "_dup_confirmed", "_dup_info", "_dup_existing_pdf_bytes",
                "_escalation_mode", "_escalation_result", "_escalation_pdf_bytes",
                "_esc_dup_pending", "_esc_dup_confirmed", "_esc_dup_info", "_esc_dup_pdf_bytes"]:
        st.session_state.pop(_k, None)

# ── Duplicate search warning / Escalation fork ───────────────────────────
if st.session_state.get("_dup_pending") and not st.session_state.get("_dup_confirmed"):
    _info = st.session_state.get("_dup_info", {})

    if not st.session_state.get("_escalation_mode"):
        # ── Three-option prompt ───────────────────────────────────────────
        st.warning(
            f"⚠️ An ROV was already generated for this address on **{_info.get('when', 'recently')}** "
            f"by **{_info.get('who', 'a user')}** ({_info.get('count', 0)} comps used). "
            f"How would you like to proceed?"
        )

        # Build the existing ROV PDF once for the direct download button
        if "_dup_existing_pdf_bytes" not in st.session_state:
            _stored_json_dl = _info.get("agent_json") or {}
            try:
                from pdf_builder import build_rov_pdf as _build_rov_pdf
                with tempfile.TemporaryDirectory() as _dup_tmp:
                    _dup_out = os.path.join(_dup_tmp, "rov.pdf")
                    _build_rov_pdf("Main_ROV_blank.pdf", _stored_json_dl, _dup_out)
                    with open(_dup_out, "rb") as _dup_f:
                        st.session_state["_dup_existing_pdf_bytes"] = _dup_f.read()
            except Exception:
                st.session_state["_dup_existing_pdf_bytes"] = b""

        _dup_c1, _dup_c2, _dup_c3 = st.columns(3)
        with _dup_c1:
            _dup_dl_addr = st.session_state.get("subject_address", "").replace(" ", "_")
            st.download_button(
                label="Download Existing ROV",
                data=st.session_state.get("_dup_existing_pdf_bytes", b""),
                file_name=f"ROV_{_dup_dl_addr}.pdf",
                mime="application/pdf",
                use_container_width=True,
                type="primary",
                key="_dup_download_existing",
            )
        with _dup_c2:
            if st.button("Generate New ROV", key="_dup_yes", use_container_width=True, type="secondary"):
                st.session_state["_dup_confirmed"] = True
                st.session_state.pop("_dup_pending", None)
                st.session_state.pop("_dup_existing_pdf_bytes", None)
                st.rerun()
        with _dup_c3:
            if st.button("Fill Out Escalation Form", key="_esc_btn", use_container_width=True, type="secondary"):
                _stored_json = _info.get("agent_json") or {}
                _form_fields = _stored_json.get("form_fields", {}) if isinstance(_stored_json, dict) else {}
                st.session_state["_escalation_mode"] = True
                st.session_state["_escalation_address"] = _info.get("address", "")
                st.session_state["_escalation_loan_number"] = _form_fields.get("loan_#", "")
                st.session_state["_escalation_borrower_name"] = _form_fields.get("borrower", "")
                st.session_state["_escalation_rov_json"] = _stored_json
                st.session_state.pop("_escalation_result", None)
                st.session_state.pop("_escalation_pdf_bytes", None)
                st.session_state.pop("_esc_dup_pending", None)
                st.session_state.pop("_esc_dup_confirmed", None)
                st.session_state.pop("_dup_existing_pdf_bytes", None)
                st.rerun()
        st.stop()

    else:
        # ── Escalation Form UI ────────────────────────────────────────────
        import tempfile

        _esc_addr = st.session_state.get("_escalation_address", "")
        _esc_loan = st.session_state.get("_escalation_loan_number", "")
        _esc_borrower = st.session_state.get("_escalation_borrower_name", "")

        st.markdown("### Appraisal Escalation")
        st.info(
            f"Generating an escalation for **{_esc_addr}**. "
            "Upload the post-ROV appraisal PDF, then click Generate Escalation."
        )

        _col_back, _ = st.columns([1, 5])
        with _col_back:
            if st.button("← Back", key="_esc_back"):
                for _k in ["_escalation_mode", "_escalation_result", "_escalation_pdf_bytes",
                            "_esc_dup_pending", "_esc_dup_confirmed", "_esc_dup_info", "_esc_dup_pdf_bytes",
                            "_dup_pending", "_dup_confirmed", "_dup_info", "_dup_existing_pdf_bytes"]:
                    st.session_state.pop(_k, None)
                st.rerun()

        # ── Escalation duplicate check (runs immediately on form load) ───
        if not st.session_state.get("_esc_dup_confirmed") and not st.session_state.get("_esc_dup_pending"):
            try:
                _esc_existing = find_recent_escalation(_esc_addr, days=60)
            except Exception:
                _esc_existing = None
            if _esc_existing:
                st.session_state["_esc_dup_pending"] = True
                st.session_state["_esc_dup_info"] = _esc_existing

        if st.session_state.get("_esc_dup_pending"):
            _esc_dup_info = st.session_state.get("_esc_dup_info", {})
            _esc_dup_when = _esc_dup_info.get("generated_at")
            _esc_dup_when_str = _esc_dup_when.strftime("%b %d, %Y at %I:%M %p") if hasattr(_esc_dup_when, "strftime") else str(_esc_dup_when or "recently")
            st.warning(
                f"⚠️ An escalation was already generated for **{_esc_addr}** on **{_esc_dup_when_str}**. "
                "Download the existing one or generate a new one."
            )

            # Build the existing PDF once so we can offer a direct download_button
            if "_esc_dup_pdf_bytes" not in st.session_state:
                _existing_res = _esc_dup_info.get("agent_json") or {}
                try:
                    import io as _io
                    from reportlab.lib.pagesizes import LETTER  # already imported inside builder
                    with tempfile.TemporaryDirectory() as _esc_tmp_dup:
                        _esc_out_dup = os.path.join(_esc_tmp_dup, "escalation.pdf")
                        escalation_pdf_builder.build_escalation_pdf(_existing_res, _esc_out_dup)
                        with open(_esc_out_dup, "rb") as _ef_dup:
                            st.session_state["_esc_dup_pdf_bytes"] = _ef_dup.read()
                except Exception as _dup_pdf_err:
                    st.error(f"Failed to load existing PDF: {_dup_pdf_err}")

            _, _esc_dc1, _esc_dc2, _ = st.columns([2, 1, 1, 2])
            with _esc_dc1:
                _esc_dup_fname = f"Escalation_{_esc_addr.replace(' ', '_')}.pdf"
                st.download_button(
                    label="Download Existing",
                    data=st.session_state.get("_esc_dup_pdf_bytes", b""),
                    file_name=_esc_dup_fname,
                    mime="application/pdf",
                    use_container_width=True,
                    type="primary",
                    key="_esc_dup_download",
                )
            with _esc_dc2:
                if st.button("Generate New One", key="_esc_dup_new", use_container_width=True, type="secondary"):
                    st.session_state["_esc_dup_confirmed"] = True
                    st.session_state.pop("_esc_dup_pending", None)
                    st.session_state.pop("_esc_dup_pdf_bytes", None)
                    st.rerun()
            st.stop()

        _esc_file = st.file_uploader(
            "Upload Post-ROV Appraisal PDF",
            type="pdf",
            key="_esc_pdf_uploader",
            help="The appraisal received after the ROV was submitted.",
        )

        _esc_selected_pages = None
        if _esc_file is not None:
            import fitz
            import base64 as _b64

            _esc_file_key = f"esc_pdf_thumbs_{_esc_file.name}_{_esc_file.size}"
            if _esc_file_key != st.session_state.get("_esc_pdf_thumb_key"):
                with st.spinner("Rendering PDF pages…"):
                    _esc_doc = fitz.open(stream=_esc_file.getvalue(), filetype="pdf")
                    _esc_thumbs = []
                    for _epg in _esc_doc:
                        _epix = _epg.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), colorspace=fitz.csRGB, alpha=False)
                        _esc_thumbs.append(_epix.tobytes("png"))
                    _esc_doc.close()
                for _k in list(st.session_state.keys()):
                    if _k.startswith("_esc_pgchk_") or _k.startswith("_esc_pdf_hires_"):
                        del st.session_state[_k]
                st.session_state["_esc_pdf_thumb_key"] = _esc_file_key
                st.session_state["_esc_pdf_thumbs"] = _esc_thumbs
                st.session_state["_esc_pdf_raw_bytes"] = _esc_file.getvalue()
                st.session_state["_esc_pdf_sel_pages"] = {1}

            _esc_thumbs = st.session_state["_esc_pdf_thumbs"]
            _esc_total_pages = len(_esc_thumbs)
            _esc_current_sel = st.session_state.get("_esc_pdf_sel_pages", {1})

            _esc_sel_count = len(_esc_current_sel)
            _esc_sel_label = ", ".join(str(p) for p in sorted(_esc_current_sel)) if _esc_current_sel else "none"

            st.markdown(
                f"**📄 {_esc_total_pages} pages** — click a page to preview. "
                f"<b>{_esc_sel_count} selected</b> ({_esc_sel_label})",
                unsafe_allow_html=True,
            )

            st.markdown("""
            <style>
            .pdf-thumb { border-radius: 6px; border: 3px solid transparent; width: 100%; cursor: pointer; }
            .pdf-thumb.selected { border-color: #4CAF50; }
            </style>
            """, unsafe_allow_html=True)

            @st.dialog("Page Preview", width="large")
            def _show_esc_page_preview(pg_num: int):
                st.markdown(f"**Page {pg_num}**")
                _hires_key = f"_esc_pdf_hires_{pg_num}"
                if _hires_key not in st.session_state:
                    _esc_hdoc = fitz.open(stream=st.session_state["_esc_pdf_raw_bytes"], filetype="pdf")
                    _esc_hpg = _esc_hdoc[pg_num - 1]
                    _esc_hpix = _esc_hpg.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), colorspace=fitz.csRGB, alpha=False)
                    st.session_state[_hires_key] = _esc_hpix.tobytes("png")
                    _esc_hdoc.close()
                st.image(st.session_state[_hires_key], use_container_width=True)

            _ESC_COLS = 5
            with st.container(height=600):
                for _row_start in range(0, _esc_total_pages, _ESC_COLS):
                    _row_end = min(_row_start + _ESC_COLS, _esc_total_pages)
                    _cols = st.columns(_ESC_COLS)
                    for _ci, _pg_idx in enumerate(range(_row_start, _row_end)):
                        _pg_num = _pg_idx + 1
                        _is_sel = _pg_num in _esc_current_sel
                        with _cols[_ci]:
                            if st.button(
                                f"Page {_pg_num}",
                                key=f"_esc_pgview_{_pg_num}",
                                use_container_width=True,
                                type="tertiary",
                            ):
                                _show_esc_page_preview(_pg_num)
                            _img_b64 = _b64.b64encode(_esc_thumbs[_pg_idx]).decode()
                            _sel_class = "selected" if _is_sel else ""
                            st.markdown(
                                f'<img class="pdf-thumb {_sel_class}" '
                                f'src="data:image/png;base64,{_img_b64}" />',
                                unsafe_allow_html=True,
                            )
                            _checked = st.checkbox(
                                "Include",
                                value=_is_sel,
                                key=f"_esc_pgchk_{_pg_num}",
                            )
                            if _checked:
                                _esc_current_sel.add(_pg_num)
                            else:
                                _esc_current_sel.discard(_pg_num)

            st.session_state["_esc_pdf_sel_pages"] = _esc_current_sel
            _esc_selected_pages = sorted(_esc_current_sel) if _esc_current_sel else None

            if not _esc_selected_pages:
                st.warning("Please select at least one page.")

        # Seed defaults once so user edits persist across reruns
        st.session_state.setdefault("_esc_ln_input", _esc_loan)
        st.session_state.setdefault("_esc_addr_input", _esc_addr)
        st.session_state.setdefault("_esc_bn_input", _esc_borrower)

        _ec1, _ec2 = st.columns(2)
        with _ec1:
            _esc_loan = st.text_input("Loan Number", key="_esc_ln_input")
            _esc_addr = st.text_input("Address", key="_esc_addr_input")
        with _ec2:
            _esc_borrower = st.text_input("Borrower Name", key="_esc_bn_input")

        _esc_generate = st.button(
            "Generate Escalation",
            type="primary",
            use_container_width=True,
            disabled=not (_esc_file is not None and _esc_selected_pages),
            key="_esc_generate_btn",
        )

        if _esc_generate and _esc_file is not None and _esc_selected_pages:
            # Clear any prior run before starting a new one
            st.session_state.pop("_escalation_result", None)
            st.session_state.pop("_escalation_pdf_bytes", None)

            from pypdf import PdfReader as _EscPdfR, PdfWriter as _EscPdfW
            import io as _esc_io
            _esc_src = _EscPdfR(_esc_io.BytesIO(_esc_file.getvalue()))
            _esc_writer = _EscPdfW()
            for _epn in _esc_selected_pages:
                _esc_writer.add_page(_esc_src.pages[_epn - 1])
            _esc_buf = _esc_io.BytesIO()
            _esc_writer.write(_esc_buf)
            _esc_pdf_bytes = _esc_buf.getvalue()

            _esc_input_payload = {
                "address": _esc_addr,
                "loan_number": _esc_loan,
                "borrower_name": _esc_borrower,
                "pdf_filename": _esc_file.name,
                "pages_sent": _esc_selected_pages,
                "rov_agent_json": st.session_state.get("_escalation_rov_json"),
            }
            st.session_state["_escalation_input_payload"] = _esc_input_payload

            with st.spinner("Analyzing appraisal and building escalation form… This may take a minute."):
                try:
                    _esc_result = escalation_agent.run_escalation_agent(
                        pdf_bytes=_esc_pdf_bytes,
                        loan_number=_esc_loan,
                        borrower_name=_esc_borrower,
                        property_address=_esc_addr,
                        rov_agent_json=st.session_state.get("_escalation_rov_json"),
                        api_key=os.getenv("ANTHROPIC_API_KEY"),
                    )
                    st.session_state["_escalation_result"] = _esc_result
                except Exception as _esc_err:
                    st.error(f"Failed to analyze appraisal: {_esc_err}")
                    with st.expander("Error details"):
                        st.exception(_esc_err)

        if "_escalation_result" in st.session_state:
            _res = st.session_state["_escalation_result"]

            if "_escalation_pdf_bytes" not in st.session_state:
                with tempfile.TemporaryDirectory() as _esc_tmp:
                    _esc_out = os.path.join(_esc_tmp, "escalation.pdf")
                    try:
                        _user_info = get_user()
                        _res["input_coordinator"] = (_user_info or {}).get("name") or (_user_info or {}).get("username") or "N/A"
                        escalation_pdf_builder.build_escalation_pdf(_res, _esc_out)
                        with open(_esc_out, "rb") as _ef:
                            st.session_state["_escalation_pdf_bytes"] = _ef.read()
                        try:
                            log_escalation(
                                address=_esc_addr,
                                loan_number=_esc_loan,
                                borrower_name=_esc_borrower,
                                agent_json=_res,
                                input_payload=st.session_state.get("_escalation_input_payload"),
                            )
                        except Exception:
                            pass
                    except Exception as _pdf_err:
                        st.error(f"Failed to generate PDF: {_pdf_err}")
                        with st.expander("Error details"):
                            st.exception(_pdf_err)

        if "_escalation_pdf_bytes" in st.session_state:
            _esc_fname = f"Escalation_{_esc_addr.replace(' ', '_')}.pdf"
            st.download_button(
                label="📄 Download Escalation PDF",
                data=st.session_state["_escalation_pdf_bytes"],
                file_name=_esc_fname,
                mime="application/pdf",
                use_container_width=True,
                type="primary",
                key="_esc_download",
            )

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
            _recent = find_recent_search(_search_addr, days=60)
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

    # ── PropertyRadar enrichment ──────────────────────────────────────────
    _pr_apns_queried = int(comps["apn"].notna().sum()) if "apn" in comps.columns else 0
    comps, _pr_apns_returned, _pr_debug_log = comp_enrichment.merge_pr_enrichment(comps)

    # Cache results in session state
    st.session_state["_results_subject"] = subject
    st.session_state["_results_comps"] = comps
    st.session_state["_filter_fingerprint"] = _filter_fingerprint
    st.session_state["_last_search_street"] = street_address
    st.session_state["_last_search_zip"] = zip_code
    # Clear manual comps on new search/filter change
    st.session_state["_manual_comps"] = []
    st.session_state["_manual_comps_synced"] = 0
    st.session_state["_manual_comps_enriched"] = []

    # Log the search (only on explicit searches, not filter reloads)
    if not _reload_clicked:
        try:
            _user = get_user()
            _enrichment_summary = comp_enrichment.enrichment_summary(
                comps, _pr_apns_queried, _pr_apns_returned, _pr_debug_log
            )
            _search_id = log_comp_search(
                user_email=(_user or {}).get("email", "unknown"),
                subject_address=f"{street_address}, {zip_code}",
                filters=advanced_filters,
                result_count=len(comps),
                enrichment_summary=_enrichment_summary,
            )
            if _search_id:
                st.session_state["_last_search_id"] = _search_id
                st.session_state["_last_enrichment"] = _enrichment_summary
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
    # Reset the form if a comp was just added (must happen before widget renders)
    if st.session_state.pop("_mc_form_reset", False):
        st.session_state["_mc_addr_query"] = ""
        st.session_state.pop("_mc_addr_select", None)

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
                    st.session_state["_mc_form_reset"] = True
                    st.rerun()
else:
    if st.session_state.pop("_mc_form_reset", False):
        st.session_state["_manual_comp_addr"] = ""
        st.session_state["_manual_comp_zip"] = ""
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
                st.session_state["_mc_form_reset"] = True
                st.rerun()

# Merge manual comps into the main comps dataframe
if st.session_state.get("_manual_comps"):
    _manual_count = len(st.session_state["_manual_comps"])
    _synced_count = st.session_state.get("_manual_comps_synced", 0)

    # Only call PR API for NEW manual comps (not on every rerun)
    if _manual_count > _synced_count:
        # Enrich only the newly added comps
        _new_comps = st.session_state["_manual_comps"][_synced_count:]
        _new_df = pd.DataFrame(_new_comps)
        for _needed_col in ["price_per_sqft", "similarity", "match_tier", "apn_mls", "data_source"]:
            if _needed_col not in _new_df.columns:
                if _needed_col == "price_per_sqft":
                    _new_df[_needed_col] = _new_df.apply(
                        lambda r: r["sale_price"] / r["sqft"] if pd.notna(r.get("sale_price")) and pd.notna(r.get("sqft")) and r.get("sqft", 0) > 0 else 0, axis=1)
                elif _needed_col == "similarity":
                    _new_df[_needed_col] = 0
                elif _needed_col == "match_tier":
                    _new_df[_needed_col] = "Manual"
                elif _needed_col == "data_source":
                    _new_df[_needed_col] = "Manual"
                else:
                    _new_df[_needed_col] = ""
        _manual_apns_queried = int(_new_df["apn"].notna().sum()) if "apn" in _new_df.columns else 0
        _new_df, _manual_pr_returned, _manual_debug_log = comp_enrichment.merge_pr_enrichment(_new_df)

        # Cache enriched results in session state
        _cached = st.session_state.setdefault("_manual_comps_enriched", [])
        _cached.extend(_new_df.to_dict(orient="records"))
        st.session_state["_manual_comps_synced"] = _manual_count

        # Update DB record
        _last_sid = st.session_state.get("_last_search_id")
        _last_enr = st.session_state.get("_last_enrichment")
        if _last_sid and _last_enr:
            try:
                _last_enr["total_comps"] = _last_enr.get("total_comps", 0) + len(_new_df)
                _last_enr["pr_apns_queried"] = _last_enr.get("pr_apns_queried", 0) + _manual_apns_queried
                _last_enr["pr_apns_returned"] = _last_enr.get("pr_apns_returned", 0) + _manual_pr_returned
                _last_enr["pr_enriched_count"] = _last_enr.get("pr_enriched_count", 0) + int(_new_df["pr_enriched"].sum()) if "pr_enriched" in _new_df.columns else _last_enr.get("pr_enriched_count", 0)
                _last_enr.setdefault("debug_log", []).extend(_manual_debug_log)
                update_enrichment_stats(_last_sid, _last_enr)
                st.session_state["_last_enrichment"] = _last_enr
            except Exception:
                pass

    # On every rerun, merge cached enriched manual comps into the display dataframe
    if st.session_state.get("_manual_comps_enriched"):
        _manual_df = pd.DataFrame(st.session_state["_manual_comps_enriched"])
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

import html as _htmlesc
_sv = lambda val, fmt=None: (
    "N/A" if (val is None or (isinstance(val, float) and pd.isna(val)))
    else (f"${float(val):,.0f}" if fmt == "$" else (f"{int(val):,}" if fmt == "," else str(val)))
)
_subj_addr_esc = _htmlesc.escape(
    f"📍  {subject.get('address', '')}  ·  {subject.get('city', '')} {subject.get('state', '')} {subject.get('zipcode', '')}"
)
_subj_fields_html = "".join(
    f'<div class="subj-field"><span class="subj-lbl">{lbl}</span><span class="subj-val">{_htmlesc.escape(val)}</span></div>'
    for lbl, val in [
        ("Beds",     _sv(subject.get("bedrooms"))),
        ("Baths",    _sv(subject.get("bathrooms"))),
        ("SqFt",     _sv(subject.get("sqft"), ",")),
        ("Lot",      _sv(subject.get("lot_size"), ",")),
        ("Yr Built", _sv(subject.get("year_built"))),
        ("AVM",      _sv(subject.get("avm_value"), "$")),
        ("Type",     _sv(subject.get("property_type"))),
    ]
)
_subj_bar_html = f'<div class="subj-bar"><span class="subj-addr">{_subj_addr_esc}</span>{_subj_fields_html}</div>'

_table_html = (
    '<style>'
    '*{box-sizing:border-box;margin:0;padding:0;}'
    'html,body{height:100%;}'
    'body{display:flex;flex-direction:column;}'
    '.tw{flex:1;min-height:0;width:100%;overflow:auto;font-family:-apple-system,BlinkMacSystemFont,sans-serif;font-size:13px;}'
    'table{border-collapse:separate;border-spacing:0;width:100%;min-width:1200px;}'
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
    '.edit-icon{cursor:pointer;margin-left:4px;font-size:11px;color:#38bdf8;opacity:.75;vertical-align:middle;display:inline-block;}'
    '.edit-icon:hover{opacity:1;color:#7dd3fc;}'
    '.edit-inp{background:#1e293b;color:#e2e8f0;border:1px solid #38bdf8;border-radius:3px;padding:2px 5px;width:90px;font-size:12px;outline:none;}'
    '.subj-bar{position:sticky;top:0;z-index:3;background:#060f1e;border-bottom:2px solid #38bdf8;padding:7px 14px;display:flex;align-items:center;gap:16px;flex-wrap:wrap;}'
    '.subj-addr{font-size:12px;color:#38bdf8;font-weight:700;white-space:nowrap;flex-shrink:0;}'
    '.subj-field{display:flex;flex-direction:column;line-height:1.25;}'
    '.subj-lbl{font-size:9px;color:#64748b;text-transform:uppercase;letter-spacing:.06em;}'
    '.subj-val{font-size:12px;color:#e2e8f0;font-weight:600;white-space:nowrap;}'
    '</style>'
    + _subj_bar_html +
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
    'const dispCols=' + _json.dumps(display_cols) + ';'
    'const tierBg={"Green":"#166534","Yellow":"#a16207","Red":"#991b1b"};'
    'let showSelOnly=false;'
    'const hdr=document.getElementById("hdr");'
    'const thSel=document.createElement("th");thSel.textContent="✔";thSel.style.width="36px";thSel.style.textAlign="center";hdr.appendChild(thSel);'
    'hdrs.forEach(h=>{const th=document.createElement("th");th.textContent=h;hdr.appendChild(th);});'
    'const tbody=document.getElementById("tbody");'
    'const allTrs=[];'
    'const noEdit=new Set(["distance_miles","similarity","match_tier"]);'
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
    '  r.cells.forEach(function(v,ci){'
    '    const td=document.createElement("td");'
    '    if(numManual>0&&i>=rows.length-numManual&&v==="N/A"&&!noEdit.has(dispCols[ci])){'
    '      const sna=document.createElement("span");sna.textContent="N/A";'
    '      const sic=document.createElement("span");sic.className="edit-icon";sic.title="Edit value";sic.textContent="✏";'
    '      sic.onclick=(function(ri,ci2){return function(){startEdit(sic,ri,ci2);};})(i,ci);'
    '      td.appendChild(sna);td.appendChild(sic);'
    '    }else{td.textContent=v;}'
    '    tr.appendChild(td);'
    '  });'
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
    'function updateCount(){'
    '  const cnt=document.querySelectorAll(".sel-chk:checked").length;'
    '  document.getElementById("selCnt").textContent=cnt;'
    '  const sel=[];document.querySelectorAll(".sel-chk").forEach((c,i)=>{if(c.checked)sel.push(i);});'
    '  try{window.parent.__comp_selection=JSON.stringify(sel);}catch(e){}'
    '}\n'
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
    'function startEdit(el,ri,ci){'
    '  const td=el.parentElement;'
    '  const cn=dispCols[ci];'
    '  function restoreIcon(){'
    '    const s=document.createElement("span");s.textContent="N/A";'
    '    const ic=document.createElement("span");ic.className="edit-icon";ic.title="Edit value";ic.textContent="✏";'
    '    ic.onclick=function(){startEdit(ic,ri,ci);};'
    '    td.innerHTML="";td.appendChild(s);td.appendChild(ic);'
    '  }'
    '  const inp=document.createElement("input");'
    '  inp.type="text";inp.className="edit-inp";inp.placeholder="Enter value";'
    '  inp.onblur=function(){'
    '    const v=inp.value.trim();'
    '    if(!v){restoreIcon();return;}'
    '    td.textContent=v;'
    '    try{'
    '      if(!window.parent.__comp_edits)window.parent.__comp_edits={};'
    '      if(!window.parent.__comp_edits[ri])window.parent.__comp_edits[ri]={};'
    '      window.parent.__comp_edits[ri][cn]=v;'
    '      window.parent.__comp_edits_json=JSON.stringify(window.parent.__comp_edits);'
    '    }catch(ex){}'
    '  };'
    '  inp.onkeydown=function(e){'
    '    if(e.key==="Enter")inp.blur();'
    '    else if(e.key==="Escape")restoreIcon();'
    '  };'
    '  td.innerHTML="";td.appendChild(inp);inp.focus();'
    '}'
    '</script>'
)

_num_rows = len(comps)
_table_height = min(110 + _num_rows * 68, 860)
_components.html(_table_html, height=_table_height, scrolling=True)

# Tier legend
st.markdown(
    "🟢 **Green** = Strong match (≥80%, no red fields)  ·  "
    "🟡 **Yellow** = Moderate match (50–79%)  ·  "
    "🔴 **Red** = Weak match (<50%)",
)

# ── Manual comp cell edit sync ────────────────────────────────────────
_edit_sync_ver = st.session_state.get("_edit_sync_ver", 0)
if st.session_state.get("_manual_comps_enriched"):
    if st.button("✅ Apply Manual Edits", key="_apply_edits_btn",
                 help="Click after editing N/A cells in the table above to save your changes"):
        st.session_state["_edit_sync_ver"] = _edit_sync_ver + 1
        st.rerun()

if _edit_sync_ver > 0:
    from streamlit_js_eval import streamlit_js_eval as _sje_edits
    _js_edits_raw = _sje_edits(
        js_expressions="window.parent.__comp_edits_json || null",
        key=f"_comp_edits_v{_edit_sync_ver}",
    )
    if _js_edits_raw is not None:
        st.session_state["_edit_sync_ver"] = 0
        if _js_edits_raw and _js_edits_raw != "null":
            try:
                import json as _je
                _edits = _je.loads(_js_edits_raw)
                _enriched = list(st.session_state.get("_manual_comps_enriched", []))
                _base_count = len(st.session_state.get("_results_comps", pd.DataFrame()))
                for _ridx_s, _col_map in _edits.items():
                    _midx = int(_ridx_s) - _base_count
                    if 0 <= _midx < len(_enriched):
                        _int_cols = {"bedrooms", "year_built", "stories"}
                        _float_cols = {"bathrooms", "sqft", "above_grade_sqft", "lot_size",
                                       "sale_price", "price_per_sqft", "distance_miles", "similarity"}
                        for _col, _raw in _col_map.items():
                            _clean = str(_raw).replace(",", "").replace("$", "").strip()
                            try:
                                if _col in _int_cols:
                                    _enriched[_midx][_col] = int(float(_clean))
                                elif _col in _float_cols:
                                    _enriched[_midx][_col] = float(_clean)
                                else:
                                    _enriched[_midx][_col] = _raw
                            except (ValueError, TypeError):
                                _enriched[_midx][_col] = _raw
                        # Auto-recalc price_per_sqft when sale_price or sqft edited
                        if ("sale_price" in _col_map or "sqft" in _col_map) and "price_per_sqft" not in _col_map:
                            _sp = _enriched[_midx].get("sale_price")
                            _sf = _enriched[_midx].get("sqft")
                            if _sp and _sf and float(_sf) > 0:
                                _enriched[_midx]["price_per_sqft"] = float(_sp) / float(_sf)
                st.session_state["_manual_comps_enriched"] = _enriched
                st.rerun()
            except Exception:
                pass

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
st.subheader("Export Appraisal Rebuttal PDF")

# Build address labels for the multiselect
_addr_labels = []
for _i, _r in comps.iterrows():
    _price = f"${_r['sale_price']:,.0f}" if pd.notna(_r.get("sale_price")) else "N/A"
    _sim = f"{_r['similarity']:.0f}%" if pd.notna(_r.get("similarity")) else ""
    _addr_labels.append(f"{_r.get('address', 'N/A')}, {_r.get('city', '')} — {_price} ({_sim} match)")

# JS bridge: must run BEFORE multiselect so session_state is set before widget renders
_sync_ver = st.session_state.get("_sel_sync_ver", 0)
if _sync_ver > 0:
    from streamlit_js_eval import streamlit_js_eval
    _js_sel = streamlit_js_eval(
        js_expressions="window.parent.__comp_selection || '[]'",
        key=f"_comp_sel_v{_sync_ver}",
    )
    if _js_sel and isinstance(_js_sel, str):
        try:
            import json as _j2
            _table_sel_indices = _j2.loads(_js_sel)
            _new_labels = [_addr_labels[i] for i in _table_sel_indices if i < len(_addr_labels)]
            if _new_labels != st.session_state.get("_rov_comp_select", []):
                st.session_state["_rov_comp_select"] = _new_labels
                st.rerun()
        except Exception:
            pass

# Multiselect + sync button on same row
_ms_col, _btn_col = st.columns([5, 1])
with _ms_col:
    selected_labels = st.multiselect(
        "Comps for PDF",
        options=_addr_labels,
        placeholder="Choose one or more comparable properties…",
        key="_rov_comp_select",
    )
with _btn_col:
    st.markdown("<div style='margin-top:1.65rem'></div>", unsafe_allow_html=True)
    if st.button("From Table ", use_container_width=True):
        st.session_state["_sel_sync_ver"] = st.session_state.get("_sel_sync_ver", 0) + 1
        st.rerun()

if selected_labels:
    selected_indices = [_addr_labels.index(lbl) for lbl in selected_labels]
    selected_comps = comps.iloc[selected_indices].copy()
else:
    selected_comps = None

if selected_comps is not None and not selected_comps.empty:
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

                # Store in session state for revision workflow
                st.session_state["_rov_result"] = result
                st.session_state["_rov_pdf_bytes"] = pdf_bytes
                st.session_state["_rov_pdf_bytes_original"] = pdf_bytes
                st.session_state["_rov_revision_count"] = 0
                st.session_state["_rov_payload"] = payload
                st.session_state["_rov_appraisal_bytes"] = appraisal_file.getvalue()
                st.session_state["_rov_selected_pages"] = selected_pages
                st.session_state["_rov_subject_addr"] = subject.get("address", "property")
                st.session_state["_rov_comps_count"] = len(selected_comps)

                # Log the ROV report
                try:
                    _rov_user = get_user()
                    _report_id = log_rov_report(
                        user_email=(_rov_user or {}).get("email", "unknown"),
                        subject_address=subject.get("address", ""),
                        comps_count=len(selected_comps),
                        agent_json=result["agent_output"],
                        input_payload=payload,
                    )
                    st.session_state["_rov_report_id"] = _report_id
                except Exception:
                    st.session_state["_rov_report_id"] = None

            except Exception as e:
                st.error(f"Failed to generate ROV: {e}")
                with st.expander("Error details"):
                    st.exception(e)

    # ── Display generated ROV + revision UI ───────────────────────────────
    if "_rov_pdf_bytes" in st.session_state:
        _subj_addr = st.session_state.get("_rov_subject_addr", "property").replace(" ", "_")
        _rev_count = st.session_state.get("_rov_revision_count", 0)
        if _rev_count > 0:
            st.success(f"✅ ROV revised (v{_rev_count + 1}) with {st.session_state.get('_rov_comps_count', 0)} comparable properties")
            _dl_col1, _dl_col2 = st.columns(2)
            with _dl_col1:
                st.download_button(
                    label=f"📄 Download Revised ROV (v{_rev_count + 1})",
                    data=st.session_state["_rov_pdf_bytes"],
                    file_name=f"ROV_{_subj_addr}_v{_rev_count + 1}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    type="primary",
                )
            with _dl_col2:
                st.download_button(
                    label="📄 Download Original ROV",
                    data=st.session_state["_rov_pdf_bytes_original"],
                    file_name=f"ROV_{_subj_addr}_original.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                    type="secondary",
                )
        else:
            st.success(f"✅ ROV generated with {st.session_state.get('_rov_comps_count', 0)} comparable properties")
            st.download_button(
                label="📄 Download Filled ROV PDF",
                data=st.session_state["_rov_pdf_bytes"],
                file_name=f"ROV_{_subj_addr}.pdf",
                mime="application/pdf",
                use_container_width=True,
                type="primary",
            )

        # ── Revision text area ────────────────────────────────────────────
        st.markdown("---")
        st.markdown("**Want to refine the ROV?** Add your edit suggestions below and regenerate.")
        _revision_notes = st.text_area(
            "Edit Suggestions",
            placeholder="e.g. Emphasize the superior lot size of comp #2, mention the recent renovation...",
            key="_rov_revision_notes",
        )
        _regen_clicked = st.button(
            "🔄 Regenerate with Edits",
            disabled=not (_revision_notes and _revision_notes.strip()),
            use_container_width=True,
        )

        if _regen_clicked and _revision_notes.strip():
            import tempfile
            from pypdf import PdfReader as _PdfR2, PdfWriter as _PdfW2
            import io as _io2

            with tempfile.TemporaryDirectory() as _tmpdir2:
                _ap_path2 = os.path.join(_tmpdir2, "appraisal.pdf")
                _src2 = _PdfR2(_io2.BytesIO(st.session_state["_rov_appraisal_bytes"]))
                _wr2 = _PdfW2()
                for _pg in st.session_state["_rov_selected_pages"]:
                    _wr2.add_page(_src2.pages[_pg - 1])
                with open(_ap_path2, "wb") as _f2:
                    _wr2.write(_f2)

                _out_path2 = os.path.join(_tmpdir2, "revised_rov.pdf")

                try:
                    with st.spinner("Regenerating ROV with your edits..."):
                        _rev_result = generate_rov_pdf(
                            payload=st.session_state["_rov_payload"],
                            appraisal_pdf_path=_ap_path2,
                            blank_form_path="Main_ROV_blank.pdf",
                            output_path=_out_path2,
                            revision_notes=_revision_notes.strip(),
                            previous_output=st.session_state["_rov_result"]["agent_output"],
                        )

                    with open(_out_path2, "rb") as _f2:
                        _rev_pdf_bytes = _f2.read()

                    # Update session state with revised version
                    st.session_state["_rov_result"] = _rev_result
                    st.session_state["_rov_pdf_bytes"] = _rev_pdf_bytes
                    st.session_state["_rov_revision_count"] = st.session_state.get("_rov_revision_count", 0) + 1

                    # Log the revision
                    try:
                        _rov_user = get_user()
                        log_rov_revision(
                            parent_id=st.session_state.get("_rov_report_id") or 0,
                            user_email=(_rov_user or {}).get("email", "unknown"),
                            subject_address=st.session_state.get("_rov_subject_addr", ""),
                            comps_count=st.session_state.get("_rov_comps_count", 0),
                            revision_notes=_revision_notes.strip(),
                            revised_agent_json=_rev_result["agent_output"],
                            input_payload=st.session_state.get("_rov_payload"),
                        )
                    except Exception:
                        pass

                    st.rerun()

                except Exception as e:
                    st.error(f"Failed to regenerate ROV: {e}")
                    with st.expander("Error details"):
                        st.exception(e)
else:
    st.info("Select at least one comparable property above to generate the ROV.")