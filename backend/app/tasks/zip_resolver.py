"""Pick the task bucket for a facility (design §6.1 step 3).

1. physical address ZIP   2. postal address ZIP
3. reverse-geocode lat/long with Amazon Location (geo-places ReverseGeocode)
4. COUNTY#<county> from the location text / service areas   5. VIRTUAL
"""

from __future__ import annotations

import logging
import re

from hsds_transform.mapper import is_missing

from ..config import Settings
from ..hsds.render import Rows, zip_of_location

log = logging.getLogger(__name__)
_COUNTY = re.compile(r"\b([A-Z][a-zA-Z]+(?: [A-Z][a-zA-Z]+)?) County\b")
_BAY_COUNTIES = ("Alameda", "Contra Costa", "Marin", "Napa", "San Francisco", "San Mateo", "Santa Clara",
                 "Solano", "Sonoma", "Santa Cruz")


def reverse_geocode_zip(settings: Settings, lat: float, lon: float) -> str | None:
    if settings.is_memory:
        return None
    try:
        from .. import aws
        resp = aws.client("geo-places", settings).reverse_geocode(QueryPosition=[float(lon), float(lat)], MaxResults=1)
        items = resp.get("ResultItems") or []
        code = ((items[0].get("Address") or {}).get("PostalCode") if items else None)
        return str(code)[:5] if code else None
    except Exception as exc:
        log.warning("reverse_geocode_failed %s", type(exc).__name__)
        return None


def resolve_bucket(location_id: str, rows: Rows, settings: Settings, service_ids: list[str] | None = None) -> str:
    loc = rows.get("location", {}).get(location_id) or {}
    if loc.get("location_type") == "virtual":
        return "VIRTUAL"
    various = "various location" in f"{loc.get('name', '')} {loc.get('description', '')}".lower()
    z = None if various else zip_of_location(location_id, rows)  # a mailing ZIP is not where people go
    if z:
        return f"ZIP#{z}"
    if not various and not is_missing(loc.get("latitude")) and not is_missing(loc.get("longitude")):
        z = reverse_geocode_zip(settings, loc["latitude"], loc["longitude"])
        if z:
            return f"ZIP#{z}"
    texts = [str(loc.get(k) or "") for k in ("name", "description")]
    texts += [str(a.get("name") or "") for a in rows.get("service_area", {}).values()
              if a.get("service_id") in (service_ids or [])]
    for text in texts:
        m = _COUNTY.search(text)
        if m:
            return f"COUNTY#{m.group(1)}"
        for county in _BAY_COUNTIES:
            if county.lower() in text.lower():
                return f"COUNTY#{county}"
    return "UNKNOWN"
