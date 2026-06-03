"""
propertyradar_client.py — PropertyRadar API integration.

Used as an enrichment layer on top of HouseCanary/Snowflake comps to refresh
stale sale data where PropertyRadar has a more recent transaction on record.
"""

import os
import re
from datetime import datetime, timedelta

import pandas as pd
import requests
import streamlit as st


_BASE_URL = "https://api.propertyradar.com/v1"

_FIELDS = [
    "RadarID",
    "APN",
    "Address",
    "City",
    "State",
    "ZipFive",
    "Latitude",
    "Longitude",
    "LastTransferRecDate",
    "LastTransferType",
    "LastTransferValue",
    "SaleAmount",
    "ListingPrice",
    "ListingDate",
    "DaysOnMarket",
]


def enrich_by_apns(apns: list[str], state: str) -> pd.DataFrame:
    """Query PropertyRadar for a batch of APNs and return enrichment data.

    Args:
        apns:  List of APN strings in HouseCanary dashed format (e.g. "17-25-127-002").
        state: Two-letter state abbreviation (e.g. "MI").

    Returns:
        DataFrame with columns:
            apn, pr_address, pr_city, pr_state, pr_zip,
            pr_latitude, pr_longitude,
            pr_transfer_date, pr_transfer_value, pr_transfer_type,
            pr_listing_status, pr_listing_sold_date, pr_listing_price
        Returns an empty DataFrame on error or when no APNs are provided.
    """
    clean_apns = []
    for a in apns:
        if a is None:
            continue
        s = str(a).strip()
        # Handle float-stringified ints like "83644.0"
        if s.endswith(".0"):
            s = s[:-2]
        # Normalize whitespace runs to dashes (e.g. NJ "11  01904" → "11-01904")
        s = re.sub(r"\s+", "-", s)
        s = re.sub(r"-{2,}", "-", s)  # collapse double dashes (e.g. "X--18" → "X-18")
        if s and s.lower() != "nan":
            clean_apns.append(s)

    if not clean_apns:
        return pd.DataFrame()

    token = os.getenv("PROPERTYRADAR_API_TOKEN")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "Criteria": [
            {"name": "APN", "value": clean_apns},
            {"name": "State", "value": [state]},
            {"name": "ListingStatus", "value": ["Sold"]},
        ],
    }

    request_params = {
        "Fields": ",".join(_FIELDS),
        "Limit": len(clean_apns),
        "Purchase": 1,
    }
    request_body = {"Criteria": payload["Criteria"]}

    try:
        resp = requests.post(
            f"{_BASE_URL}/properties",
            headers=headers,
            params=request_params,
            json=request_body,
            timeout=15,
        )
        if not resp.ok:
            st.error(
                f"PropertyRadar API error: {resp.status_code} {resp.reason}\n"
                f"**Request params:** `{request_params}`\n"
                f"**Request body:** `{request_body}`\n"
                f"**Response body:** `{resp.text}`"
            )
            return pd.DataFrame()
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as exc:
        st.error(
            f"PropertyRadar API error: {exc}\n"
            f"**Request params:** `{request_params}`\n"
            f"**Request body:** `{request_body}`"
        )
        return pd.DataFrame()

    results = data.get("results", data) if isinstance(data, dict) else data
    if not results:
        return pd.DataFrame()

    rows = []
    for prop in results:
        # Compute MLS sold date: prefer ListingDate + DaysOnMarket, fallback to LastTransferRecDate when Sold
        pr_listing_sold_date = None
        try:
            listing_date = prop.get("ListingDate")
            days_on_market = prop.get("DaysOnMarket")
            if listing_date and days_on_market is not None:
                pr_listing_sold_date = (
                    datetime.strptime(str(listing_date), "%Y-%m-%d")
                    + timedelta(days=int(days_on_market))
                ).strftime("%Y-%m-%d")
        except Exception:
            pass
        # If ListingStatus is Sold but we couldn't compute date, use transfer date
        if not pr_listing_sold_date:
            if prop.get("LastTransferRecDate"):
                pr_listing_sold_date = str(prop["LastTransferRecDate"])[:10]

        rows.append({
            "apn": prop.get("APN"),
            "pr_address": prop.get("Address"),
            "pr_city": prop.get("City"),
            "pr_state": prop.get("State"),
            "pr_zip": prop.get("ZipFive"),
            "pr_latitude": prop.get("Latitude"),
            "pr_longitude": prop.get("Longitude"),
            # Deed transfer data (county records)
            "pr_transfer_date": prop.get("LastTransferRecDate"),
            "pr_transfer_value": prop.get("LastTransferValue") or prop.get("SaleAmount"),
            "pr_transfer_type": prop.get("LastTransferType"),
            # MLS listing data (often newer than deed)
            "pr_listing_status": "Sold",
            "pr_listing_sold_date": pr_listing_sold_date,
            "pr_listing_price": prop.get("ListingPrice"),
        })

    return pd.DataFrame(rows)


def enrich_by_addresses(addresses: list[dict], state: str) -> pd.DataFrame:
    """Query PropertyRadar by street address when APN lookup fails.

    Args:
        addresses: List of dicts with keys 'address' and 'zipcode'.
        state:     Two-letter state abbreviation (e.g. "MA").

    Returns:
        Same DataFrame schema as enrich_by_apns.
    """
    if not addresses:
        return pd.DataFrame()

    # Deduplicate and clean
    clean = []
    seen = set()
    for entry in addresses:
        addr = str(entry.get("address", "")).strip().upper()
        if addr and addr not in seen:
            seen.add(addr)
            clean.append(entry)

    if not clean:
        return pd.DataFrame()

    token = os.getenv("PROPERTYRADAR_API_TOKEN")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Query by Address + State + ZipFive
    # Group by zip to keep requests scoped
    from collections import defaultdict
    by_zip = defaultdict(list)
    for entry in clean:
        z = str(entry.get("zipcode", "")).strip()[:5]
        by_zip[z].append(str(entry["address"]).strip())

    all_rows = []
    for zipcode, addr_list in by_zip.items():
        criteria = [
            {"name": "Address", "value": addr_list},
            {"name": "State", "value": [state]},
            {"name": "ListingStatus", "value": ["Sold"]},
        ]
        if zipcode:
            criteria.append({"name": "ZipFive", "value": [zipcode]})

        addr_params = {
            "Fields": ",".join(_FIELDS),
            "Limit": len(addr_list),
            "Purchase": 1,
        }
        addr_body = {"Criteria": criteria}

        try:
            resp = requests.post(
                f"{_BASE_URL}/properties",
                headers=headers,
                params=addr_params,
                json=addr_body,
                timeout=15,
            )
            if not resp.ok:
                st.warning(
                    f"PropertyRadar address lookup failed (zip {zipcode}): "
                    f"{resp.status_code} {resp.reason}\n"
                    f"**Request params:** `{addr_params}`\n"
                    f"**Request body:** `{addr_body}`\n"
                    f"**Response body:** `{resp.text}`"
                )
                continue
            data = resp.json()
        except requests.exceptions.RequestException as exc:
            st.warning(f"PropertyRadar address lookup error (zip {zipcode}): {exc}")
            continue

        results = data.get("results", data) if isinstance(data, dict) else data
        if not results:
            continue

        for prop in results:
            pr_listing_sold_date = None
            try:
                listing_date = prop.get("ListingDate")
                days_on_market = prop.get("DaysOnMarket")
                if listing_date and days_on_market is not None:
                    pr_listing_sold_date = (
                        datetime.strptime(str(listing_date), "%Y-%m-%d")
                        + timedelta(days=int(days_on_market))
                    ).strftime("%Y-%m-%d")
            except Exception:
                pass
            if not pr_listing_sold_date:
                if prop.get("LastTransferRecDate"):
                    pr_listing_sold_date = str(prop["LastTransferRecDate"])[:10]

            all_rows.append({
                "apn": prop.get("APN"),
                "pr_address": prop.get("Address"),
                "pr_city": prop.get("City"),
                "pr_state": prop.get("State"),
                "pr_zip": prop.get("ZipFive"),
                "pr_latitude": prop.get("Latitude"),
                "pr_longitude": prop.get("Longitude"),
                "pr_transfer_date": prop.get("LastTransferRecDate"),
                "pr_transfer_value": prop.get("LastTransferValue") or prop.get("SaleAmount"),
                "pr_transfer_type": prop.get("LastTransferType"),
                "pr_listing_status": "Sold",
                "pr_listing_sold_date": pr_listing_sold_date,
                "pr_listing_price": prop.get("ListingPrice"),
            })

    return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()
