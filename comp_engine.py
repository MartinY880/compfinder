"""
Comp Engine
===========
Similarity scoring and ranking logic for comparable properties.
Per-field accuracy tiers: Green (exact/close), Yellow (near), Red (poor).
"""

import pandas as pd

# Tier constants
GREEN = "Green"
YELLOW = "Yellow"
RED = "Red"


def _tier_bedrooms(subject_val, comp_val) -> str:
    """Exact = Green, ±1 = Yellow, ±2+ = Red."""
    diff = abs((subject_val or 0) - (comp_val or 0))
    if diff == 0:
        return GREEN
    elif diff == 1:
        return YELLOW
    return RED


def _tier_bathrooms(subject_val, comp_val) -> str:
    """Exact = Green, ±1 = Yellow, ±2+ = Red."""
    diff = abs((subject_val or 0) - (comp_val or 0))
    if diff <= 0.5:
        return GREEN
    elif diff <= 1:
        return YELLOW
    return RED


def _tier_sqft(subject_val, comp_val) -> str:
    """1-10% diff = Green, 11-20% = Yellow, 21%+ = Red."""
    s = subject_val or 0
    c = comp_val or 0
    if s <= 0 or c <= 0:
        return GREEN  # can't compare, assume ok
    pct = abs(s - c) / s
    if pct <= 0.10:
        return GREEN
    elif pct <= 0.20:
        return YELLOW
    return RED


def _tier_year_built(subject_val, comp_val) -> str:
    """1-10% age diff = Green, 11-20% = Yellow, 21%+ = Red."""
    s = subject_val or 0
    c = comp_val or 0
    if not s or not c:
        return GREEN  # can't compare
    pct = abs(s - c) / s
    if pct <= 0.10:
        return GREEN
    elif pct <= 0.20:
        return YELLOW
    return RED


def _tier_distance(distance_miles: float, search_radius: float) -> str:
    """Within 50% of search radius = Green, beyond 50% = Yellow."""
    if search_radius <= 0:
        return GREEN
    if distance_miles <= search_radius * 0.5:
        return GREEN
    return YELLOW


def calculate_similarity(
    subject: dict,
    comp: dict,
    search_radius: float = 2.0,
) -> dict:
    """
    Calculate a 0-100 similarity score between subject and comp.
    Returns a dict with the overall score, per-field tiers, and a
    composite match tier.
    """
    score = 100.0

    # --- Property Type (hard filter — must match) ---
    if subject.get("property_type") and comp.get("property_type"):
        if str(subject["property_type"]).upper() != str(comp["property_type"]).upper():
            return {
                "similarity": 0.0,
                "tier_bedrooms": RED,
                "tier_bathrooms": RED,
                "tier_sqft": RED,
                "tier_year_built": RED,
                "tier_distance": RED,
                "match_tier": RED,
            }

    # --- Per-field tiers ---
    s_beds = subject.get("bedrooms") or 0
    c_beds = comp.get("bedrooms") or 0
    tier_beds = _tier_bedrooms(s_beds, c_beds)

    s_baths = subject.get("bathrooms") or 0
    c_baths = comp.get("bathrooms") or 0
    tier_baths = _tier_bathrooms(s_baths, c_baths)

    s_sqft = subject.get("sqft") or 0
    c_sqft = comp.get("sqft") or 0
    tier_sqft = _tier_sqft(s_sqft, c_sqft)

    s_year = subject.get("year_built") or 0
    c_year = comp.get("year_built") or 0
    tier_year = _tier_year_built(s_year, c_year)

    distance = comp.get("distance_miles", 0) or 0
    tier_dist = _tier_distance(distance, search_radius)

    # --- Score penalties (unchanged logic) ---

    # Bedrooms (weight: 15)
    bed_diff = abs(s_beds - c_beds)
    if bed_diff == 1:
        score -= 8
    elif bed_diff >= 2:
        score -= 15 + (bed_diff - 2) * 5

    # Bathrooms (weight: 10)
    bath_diff = abs(s_baths - c_baths)
    if 0.5 < bath_diff <= 1:
        score -= 5
    elif bath_diff > 1:
        score -= 10 + (bath_diff - 1) * 5

    # Square Footage (weight: 20)
    if s_sqft > 0 and c_sqft > 0:
        sqft_pct = abs(s_sqft - c_sqft) / s_sqft
        if sqft_pct <= 0.05:
            pass
        elif sqft_pct <= 0.10:
            score -= 5
        elif sqft_pct <= 0.20:
            score -= 12
        else:
            score -= 20

    # Lot Size (weight: 10)
    s_lot = subject.get("lot_size") or 0
    c_lot = comp.get("lot_size") or 0
    if s_lot > 0 and c_lot > 0:
        lot_pct = abs(s_lot - c_lot) / s_lot
        if lot_pct <= 0.10:
            pass
        elif lot_pct <= 0.25:
            score -= 5
        else:
            score -= 10

    # Year Built (weight: 10)
    if s_year and c_year:
        year_diff = abs(s_year - c_year)
        if year_diff <= 5:
            pass
        elif year_diff <= 10:
            score -= 5
        elif year_diff <= 20:
            score -= 8
        else:
            score -= 10

    # Distance penalty (weight: 15)
    if distance <= 0.25:
        pass
    elif distance <= 0.5:
        score -= 3
    elif distance <= 1.0:
        score -= 7
    elif distance <= 1.5:
        score -= 11
    else:
        score -= 15

    similarity = max(0.0, min(100.0, score))

    # --- Composite match tier ---
    tiers = [tier_beds, tier_baths, tier_sqft, tier_year, tier_dist]
    if similarity >= 80 and RED not in tiers:
        match_tier = GREEN
    elif similarity >= 50:
        match_tier = YELLOW
    else:
        match_tier = RED

    return {
        "similarity": similarity,
        "tier_bedrooms": tier_beds,
        "tier_bathrooms": tier_baths,
        "tier_sqft": tier_sqft,
        "tier_year_built": tier_year,
        "tier_distance": tier_dist,
        "match_tier": match_tier,
    }


def score_and_rank(
    subject: dict,
    comps_df: pd.DataFrame,
    min_similarity: float = 0.0,
    max_comps: int = 20,
    search_radius: float = 2.0,
) -> pd.DataFrame:
    """
    Score every candidate comp, filter by minimum similarity, and return
    the top results sorted by similarity (desc) then distance (asc).
    Adds per-field tier columns and a composite match_tier column.
    """
    if comps_df.empty:
        return comps_df

    results = []
    for _, row in comps_df.iterrows():
        results.append(calculate_similarity(subject, row.to_dict(), search_radius))

    comps_df = comps_df.copy()
    comps_df["similarity"] = [r["similarity"] for r in results]
    comps_df["tier_bedrooms"] = [r["tier_bedrooms"] for r in results]
    comps_df["tier_bathrooms"] = [r["tier_bathrooms"] for r in results]
    comps_df["tier_sqft"] = [r["tier_sqft"] for r in results]
    comps_df["tier_year_built"] = [r["tier_year_built"] for r in results]
    comps_df["tier_distance"] = [r["tier_distance"] for r in results]
    comps_df["match_tier"] = [r["match_tier"] for r in results]

    # Price per sqft
    comps_df["price_per_sqft"] = comps_df.apply(
        lambda r: (r["sale_price"] / r["sqft"]) if r.get("sqft") and r["sqft"] > 0 else None,
        axis=1,
    )

    # Filter & sort
    comps_df = comps_df[comps_df["similarity"] >= min_similarity]
    comps_df = comps_df.sort_values(
        ["similarity", "distance_miles"], ascending=[False, True]
    ).head(max_comps)

    return comps_df.reset_index(drop=True)
