"""
comp_enrichment.py — PropertyRadar enrichment layer for HouseCanary comps.

Runs automatically after score_and_rank() to refresh stale sale data where
PropertyRadar has a more current transaction on record.
"""

import re

import pandas as pd

import propertyradar_client


def _normalize_apn(apn) -> str:
    """Apply the same APN normalization as propertyradar_client.enrich_by_apns."""
    if apn is None:
        return ""
    s = str(apn).strip()
    if s.endswith(".0"):
        s = s[:-2]
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-{2,}", "-", s)  # collapse double dashes (e.g. "X--18" → "X-18")
    return s if s.lower() != "nan" else ""


def merge_pr_enrichment(comps_df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Enrich HouseCanary comps with fresher sale data from PropertyRadar.

    For each comp row, PropertyRadar is checked for a newer sale using two paths
    in order of preference:
      1. MLS listing path — used when PR has ListingStatus="Sold" and a computable
         sold date (ListingDate + DaysOnMarket). No transfer-type restriction.
      2. Deed transfer path — fallback when no valid listing. Requires
         pr_transfer_type == "Market".

    Both paths require the PR date to be strictly newer than HC's sale_date, and
    pass ZIP + state sanity checks before swapping.

    APNs are grouped by the comp's own state column so multi-state result sets
    each get a correctly scoped PropertyRadar query.

    Args:
        comps_df: DataFrame returned by score_and_rank(); must contain 'apn' and
                  'state' columns.

    Returns:
        Tuple of:
          - The (possibly modified) comps_df with a new boolean column 'pr_enriched'.
          - The total number of properties PropertyRadar returned across all states.
        Returns (comps_df, 0) unchanged if it is empty or missing required columns.
    """
    if comps_df.empty or "apn" not in comps_df.columns or "state" not in comps_df.columns:
        return comps_df, 0, []

    # One PR call per state so each batch is correctly scoped
    pr_frames = []
    for state_val, group in comps_df.groupby("state"):
        state_str = str(state_val).strip()
        if not state_str or state_str.lower() == "nan":
            continue
        apns = group["apn"].dropna().tolist()
        pr_chunk = propertyradar_client.enrich_by_apns(apns, state_str)
        if not pr_chunk.empty:
            pr_frames.append(pr_chunk)

    pr_df = pd.concat(pr_frames, ignore_index=True) if pr_frames else pd.DataFrame()

    # Address-based fallback: if APN query returned fewer results than comps,
    # query PR by address for unmatched comps (handles incompatible APN formats).
    if len(pr_df) < len(comps_df):
        # Build address index of what APN query already found
        _found_addrs = set(
            str(a).strip().upper() for a in pr_df["pr_address"] if pd.notna(a)
        ) if not pr_df.empty else set()
        # Collect comps whose address wasn't already returned
        _addr_lookups: list[dict] = []
        for state_val, group in comps_df.groupby("state"):
            state_str = str(state_val).strip()
            if not state_str or state_str.lower() == "nan":
                continue
            for _, row in group.iterrows():
                addr = str(row.get("address", "")).strip().upper()
                if addr and addr not in _found_addrs:
                    _addr_lookups.append({
                        "address": addr,
                        "zipcode": str(row.get("zipcode", "")),
                        "state": state_str,
                    })
        # Query by address, grouped by state
        if _addr_lookups:
            from collections import defaultdict
            _by_state = defaultdict(list)
            for entry in _addr_lookups:
                _by_state[entry["state"]].append(entry)
            for _st, _entries in _by_state.items():
                _addr_chunk = propertyradar_client.enrich_by_addresses(_entries, _st)
                if not _addr_chunk.empty:
                    pr_frames.append(_addr_chunk)
            pr_df = pd.concat(pr_frames, ignore_index=True) if pr_frames else pd.DataFrame()

    pr_apns_returned = len(pr_df)

    comps_df = comps_df.copy()
    comps_df["pr_enriched"] = False

    if pr_df.empty:
        return comps_df, pr_apns_returned, []

    # Normalise deed date column to datetime for comparison
    pr_df = pr_df.copy()
    pr_df["pr_transfer_date"] = pd.to_datetime(pr_df["pr_transfer_date"], errors="coerce")
    pr_df["pr_listing_sold_date"] = pd.to_datetime(pr_df["pr_listing_sold_date"], errors="coerce")

    # Index PR rows by normalized APN for fast lookup
    pr_df["_norm_apn"] = pr_df["apn"].apply(_normalize_apn)
    pr_indexed = pr_df[pr_df["_norm_apn"] != ""].set_index("_norm_apn")

    # Secondary index by normalized address for fallback when APN match fails
    pr_df["_norm_addr"] = pr_df["pr_address"].apply(
        lambda a: str(a).strip().upper() if pd.notna(a) else ""
    )
    pr_by_addr = pr_df[pr_df["_norm_addr"] != ""].set_index("_norm_addr")

    hc_sale_dates = pd.to_datetime(comps_df["sale_date"], errors="coerce")

    # Per-comp debug log — stored in enrichment_summary for DB
    _debug_log: list[dict] = []

    for idx in comps_df.index:
        raw_apn = comps_df.at[idx, "apn"]
        apn = _normalize_apn(raw_apn)
        hc_addr = str(comps_df.at[idx, "address"]).strip().upper() if "address" in comps_df.columns else ""
        comp_debug = {"address": hc_addr, "apn_raw": str(raw_apn), "apn_normalized": apn}

        # Primary: match by APN; Fallback: match by address (no extra API call)
        pr = None
        match_method = None
        if apn and apn in pr_indexed.index:
            pr = pr_indexed.loc[apn]
            match_method = "apn"
        else:
            if hc_addr and hc_addr in pr_by_addr.index:
                pr = pr_by_addr.loc[hc_addr]
                match_method = "address"

        if pr is None:
            comp_debug["result"] = "no_match"
            _debug_log.append(comp_debug)
            continue

        comp_debug["match_method"] = match_method

        # If multiple PR rows share the same APN (e.g. county-scoped APNs in NJ),
        # prefer rows whose ZIP matches the HC comp before sorting by date.
        if isinstance(pr, pd.DataFrame):
            comp_zip = str(comps_df.at[idx, "zipcode"]) if "zipcode" in comps_df.columns else ""
            if comp_zip:
                zip_match = pr[pr["pr_zip"].astype(str) == comp_zip]
                if not zip_match.empty:
                    pr = zip_match
            pr = pr.sort_values(
                ["pr_listing_sold_date", "pr_transfer_date"],
                ascending=False,
                na_position="last",
            ).iloc[0]

        hc_sale_date = hc_sale_dates.at[idx]

        # ── Determine which PR date/price to use (listing path preferred) ──
        use_listing = (
            str(pr.get("pr_listing_status", "")) == "Sold"
            and not pd.isnull(pr["pr_listing_sold_date"])
        )

        if use_listing:
            pr_date = pr["pr_listing_sold_date"]
            pr_price = pr.get("pr_listing_price")
            comp_debug["pr_path"] = "listing"
        else:
            # Deed path — skip only explicitly non-market transfers (foreclosures, gifts, etc.)
            _NON_MARKET_TYPES = {"non market", "non-market", "foreclosure", "quit claim", "gift", "tax deed"}
            _xfer_type = str(pr.get("pr_transfer_type", "") or "").strip().lower()
            if _xfer_type in _NON_MARKET_TYPES:
                comp_debug["result"] = "skip_non_market_transfer"
                comp_debug["pr_transfer_type"] = str(pr.get("pr_transfer_type", ""))
                _debug_log.append(comp_debug)
                continue
            pr_date = pr["pr_transfer_date"]
            pr_price = pr.get("pr_transfer_value")
            comp_debug["pr_path"] = "deed"
            if pd.isnull(pr_date):
                comp_debug["result"] = "skip_no_pr_date"
                _debug_log.append(comp_debug)
                continue

        comp_debug["hc_sale_date"] = str(hc_sale_date)[:10] if not pd.isnull(hc_sale_date) else None
        comp_debug["pr_date"] = str(pr_date)[:10]
        comp_debug["pr_price"] = float(pr_price) if pr_price and not pd.isnull(pr_price) else None

        # Backfill APN from PR if the comp has no valid APN (e.g. manual comps)
        _comp_apn_raw = str(comps_df.at[idx, "apn"]) if "apn" in comps_df.columns else ""
        _comp_apn_missing = not _comp_apn_raw or _comp_apn_raw in ("0", "nan", "None", "")
        if _comp_apn_missing and pr.get("apn"):
            comps_df.at[idx, "apn"] = pr["apn"]
            comp_debug["apn_backfilled"] = str(pr["apn"])

        # Swap only when PR date is strictly newer than HC (or HC has no date)
        if not pd.isnull(hc_sale_date) and pr_date <= hc_sale_date:
            # Even if not newer, mark as enriched if we backfilled APN or price is better
            if _comp_apn_missing and pr.get("apn"):
                comps_df.at[idx, "pr_enriched"] = True
                comp_debug["result"] = "enriched_apn_only"
                _debug_log.append(comp_debug)
            else:
                comp_debug["result"] = "skip_not_newer"
                _debug_log.append(comp_debug)
            continue

        # ZIP sanity check
        comp_zip = str(comps_df.at[idx, "zipcode"]) if "zipcode" in comps_df.columns else ""
        pr_zip = str(pr.get("pr_zip") or "")
        if comp_zip and pr_zip and comp_zip != pr_zip:
            comp_debug["result"] = "skip_zip_mismatch"
            comp_debug["comp_zip"] = comp_zip
            comp_debug["pr_zip"] = pr_zip
            _debug_log.append(comp_debug)
            continue

        # State sanity check
        comp_state = str(comps_df.at[idx, "state"]) if "state" in comps_df.columns else ""
        pr_state = str(pr.get("pr_state") or "")
        if comp_state and pr_state and comp_state.upper() != pr_state.upper():
            comp_debug["result"] = "skip_state_mismatch"
            _debug_log.append(comp_debug)
            continue

        # All checks passed — apply the enrichment
        comps_df.at[idx, "sale_date"] = pr_date.strftime("%Y-%m-%d")
        if pr_price and float(pr_price) > 0:
            comps_df.at[idx, "sale_price"] = float(pr_price)

        sqft = comps_df.at[idx, "sqft"] if "sqft" in comps_df.columns else None
        if sqft and float(sqft) > 0:
            comps_df.at[idx, "price_per_sqft"] = comps_df.at[idx, "sale_price"] / float(sqft)

        comps_df.at[idx, "pr_enriched"] = True
        comp_debug["result"] = "enriched"
        _debug_log.append(comp_debug)

    return comps_df, pr_apns_returned, _debug_log


def enrichment_summary(comps_df: pd.DataFrame, pr_apns_queried: int, pr_apns_returned: int, debug_log: list[dict] | None = None) -> dict:
    """Build the enrichment_summary dict for db.log_comp_search.

    Args:
        comps_df:          The enriched comps DataFrame (must have 'pr_enriched').
        pr_apns_queried:   Number of APNs sent to PropertyRadar.
        pr_apns_returned:  Number of properties returned by PropertyRadar.
        debug_log:         Per-comp enrichment debug details from merge_pr_enrichment.

    Returns:
        Dict with keys: total_comps, pr_enriched_count, pr_apns_queried, pr_apns_returned, debug_log.
    """
    enriched_count = int(comps_df["pr_enriched"].sum()) if "pr_enriched" in comps_df.columns else 0
    return {
        "total_comps": len(comps_df),
        "pr_enriched_count": enriched_count,
        "pr_apns_queried": pr_apns_queried,
        "pr_apns_returned": pr_apns_returned,
        "debug_log": debug_log or [],
    }
