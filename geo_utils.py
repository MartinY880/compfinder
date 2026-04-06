import math


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance between two lat/long points in miles."""
    R = 3958.8  # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def normalize_address(address: str) -> str:
    """Basic address normalization for matching."""
    addr = address.upper().strip()
    replacements = {
        " STREET": " ST", " AVENUE": " AVE", " BOULEVARD": " BLVD",
        " DRIVE": " DR", " LANE": " LN", " ROAD": " RD",
        " COURT": " CT", " CIRCLE": " CIR", " PLACE": " PL",
        " NORTH ": " N ", " SOUTH ": " S ",
        " EAST ": " E ", " WEST ": " W ",
    }
    for old, new in replacements.items():
        addr = addr.replace(old, new)
    return addr
