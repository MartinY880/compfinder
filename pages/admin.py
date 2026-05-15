"""
Admin Panel — ROV Reports & Search History
Requires the `manage:compfinder` Logto role.
"""

import json

import pandas as pd
import streamlit as st

from auth import require_auth, has_role, has_scope
from db import get_rov_reports, get_comp_searches

st.set_page_config(page_title="Admin — Comp Finder", page_icon="🔧", layout="wide")

require_auth()

if not (has_scope("manage:compfinder") or has_role("manage:compfinder")):
    st.error("⛔ Access denied. You need the `manage:compfinder` permission to view this page.")
    st.stop()

st.title("🔧 Admin Panel")
st.caption("ROV report history and comp search logs")

tab1, tab2 = st.tabs(["📄 ROV Reports", "🔍 Search History"])

# ── ROV Reports ───────────────────────────────────────────────────────────────
with tab1:
    try:
        reports = get_rov_reports(limit=200)
    except Exception as e:
        st.error(f"Failed to load ROV reports: {e}")
        reports = []

    if not reports:
        st.info("No ROV reports generated yet.")
    else:
        st.caption(f"{len(reports)} reports")

        # Summary table
        df = pd.DataFrame([
            {
                "ID": r["id"],
                "User": r["user_email"],
                "Subject": r["subject_address"],
                "Comps": r["comps_count"],
                "Generated": r["created_at"],
            }
            for r in reports
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)

        # Drill-down: pick a report to inspect Claude's JSON
        st.divider()
        selected_id = st.selectbox(
            "Inspect Claude JSON for report ID",
            options=[r["id"] for r in reports],
            format_func=lambda i: next(
                f"#{i} — {r['subject_address']} ({r['created_at'].strftime('%Y-%m-%d %H:%M')})"
                for r in reports if r["id"] == i
            ),
        )
        if selected_id:
            report = next(r for r in reports if r["id"] == selected_id)
            st.subheader(f"Report #{selected_id} — {report['subject_address']}")
            st.caption(f"User: {report['user_email']}  |  {report['created_at']}")

            json_tab1, json_tab2 = st.tabs(["📤 Input Payload (sent to Claude)", "📥 Claude Response"])
            with json_tab1:
                if report.get("input_payload"):
                    st.json(report["input_payload"])
                else:
                    st.info("No input payload stored for this report (generated before this feature).")
            with json_tab2:
                st.json(report["agent_json"])

# ── Search History ────────────────────────────────────────────────────────────
with tab2:
    try:
        searches = get_comp_searches(limit=200)
    except Exception as e:
        st.error(f"Failed to load search history: {e}")
        searches = []

    if not searches:
        st.info("No searches recorded yet.")
    else:
        st.caption(f"{len(searches)} searches")

        df2 = pd.DataFrame([
            {
                "ID": s["id"],
                "User": s["user_email"],
                "Subject": s["subject_address"],
                "Results": s["result_count"],
                "Searched At": s["created_at"],
            }
            for s in searches
        ])
        st.dataframe(df2, use_container_width=True, hide_index=True)

        st.divider()
        selected_search_id = st.selectbox(
            "Inspect filters for search ID",
            options=[s["id"] for s in searches],
            format_func=lambda i: next(
                f"#{i} — {s['subject_address']} ({s['created_at'].strftime('%Y-%m-%d %H:%M')})"
                for s in searches if s["id"] == i
            ),
        )
        if selected_search_id:
            search = next(s for s in searches if s["id"] == selected_search_id)
            st.subheader(f"Search #{selected_search_id} — {search['subject_address']}")
            st.caption(f"User: {search['user_email']}  |  {search['created_at']}  |  {search['result_count']} results")
            st.json(search["filters"])
