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
                "Type": "🔄 Revision" if r.get("revision_notes") else "Original",
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
            _is_revision = bool(report.get("revision_notes"))
            _meta = f"User: {report['user_email']}  |  {report['created_at']}"
            if _is_revision:
                _meta += f"  |  🔄 Revision of #{report.get('parent_id', '?')}"
            st.caption(_meta)

            _tabs = ["📤 Input Payload (sent to Claude)", "📥 Claude Response"]
            if _is_revision:
                _tabs.append("✏️ Revision Notes")
            json_tabs = st.tabs(_tabs)
            with json_tabs[0]:
                if report.get("input_payload"):
                    st.json(report["input_payload"])
                else:
                    st.info("No input payload stored for this report (generated before this feature).")
            with json_tabs[1]:
                st.json(report.get("revised_agent_json") or report["agent_json"])
            if _is_revision:
                with json_tabs[2]:
                    st.markdown(report["revision_notes"])

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

            _stab1, _stab2 = st.tabs(["🔍 Filters", "🔗 PR Enrichment Debug"])
            with _stab1:
                st.json(search["filters"])
            with _stab2:
                _enr = search.get("enrichment_stats")
                if not _enr:
                    st.info("No enrichment data stored for this search.")
                else:
                    # Summary metrics
                    _ec1, _ec2, _ec3, _ec4 = st.columns(4)
                    _ec1.metric("Total Comps", _enr.get("total_comps", 0))
                    _ec2.metric("PR Enriched", _enr.get("pr_enriched_count", 0))
                    _ec3.metric("APNs Queried", _enr.get("pr_apns_queried", 0))
                    _ec4.metric("APNs Returned", _enr.get("pr_apns_returned", 0))

                    # Per-comp debug log table
                    _dlog = _enr.get("debug_log", [])
                    if _dlog:
                        st.markdown("**Per-Comp Enrichment Detail**")
                        _dlog_df = pd.DataFrame(_dlog)
                        # Reorder columns for readability
                        _preferred_cols = ["address", "apn_raw", "apn_normalized", "match_method", "pr_path", "result", "hc_sale_date", "pr_date", "pr_price"]
                        _ordered = [c for c in _preferred_cols if c in _dlog_df.columns]
                        _ordered += [c for c in _dlog_df.columns if c not in _ordered]
                        st.dataframe(_dlog_df[_ordered], use_container_width=True, hide_index=True)
                    else:
                        st.info("No per-comp debug log available (PR may have returned no results).")

