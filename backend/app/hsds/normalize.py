"""Source-quirk fixes applied to a mapped dataset before validation.

Each fix is listed in RESOURCE-ENGINE-DESIGN.md §11 (NDP quirks) or §12 (ODRS)
and is recorded as a `Fix` so it shows up in the run report and, where it needs
a person, becomes a heuristic pre-flag (see app/verify/rules.py).

Nothing here invents facts: values are derived from the source row itself
(e.g. a taxonomy term's description falls back to its own name) and every
fallback is reported.
"""

from __future__ import annotations

import html
import re
import uuid
from dataclasses import dataclass, field

from hsds_transform.mapper import Dataset, is_missing

_TAG = re.compile(r"<[^>]+>")
_PHONE_DIGITS = re.compile(r"\d")
_DOMAINISH = re.compile(r"^(https?://)?[\w-]+(\.[\w-]+)+(/\S*)?$", re.I)
_PHONE_TYPE = {"main": "voice", "other": "voice", "voice": "voice", "fax": "fax", "cell": "cell",
               "mobile": "cell", "tty": "textphone", "textphone": "textphone", "text": "text",
               "sms": "text", "video": "video", "pager": "pager"}
_US_STATE = re.compile(r"^(A[KLRZ]|C[AOT]|D[CE]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|O[HKR]|PA|PR|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])$")
_NS = uuid.UUID("2f1c9e44-7c1f-4d64-9a8e-3c2d7b1f0a55")


@dataclass
class Fix:
    table: str
    id: str
    code: str
    detail: str = ""
    service_ids: list[str] = field(default_factory=list)
    needs_person: bool = False


def strip_html(text: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>|</p>\s*<p[^>]*>", "\n", text)
    text = html.unescape(_TAG.sub("", text))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def normalize(ds: Dataset, source: str) -> list[Fix]:
    fixes: list[Fix] = []
    rows = ds.rows

    # 1. HTML in free text (NDP descriptions are <p>…</p>).
    for table in ("organization", "service", "location", "program"):
        for row in rows.get(table, {}).values():
            for col in ("description", "eligibility_description", "application_process", "fees_description"):
                val = row.get(col)
                if isinstance(val, str) and "<" in val and ">" in val:
                    row[col] = strip_html(val)

    # 2. Required descriptions the source does not have.
    for row in rows.get("taxonomy", {}).values():
        if is_missing(row.get("description")):
            row["description"] = f"{row.get('name') or 'Source'} taxonomy ({source})"
            fixes.append(Fix("taxonomy", row["id"], "taxonomy_description_from_name"))
    for row in rows.get("taxonomy_term", {}).values():
        if is_missing(row.get("description")):
            row["description"] = str(row.get("name") or row.get("code") or "Taxonomy term")
            fixes.append(Fix("taxonomy_term", row["id"], "term_description_from_name"))
    for row in rows.get("organization", {}).values():
        if is_missing(row.get("description")):
            # ODRS orgs have no description (§12.3, option 3 fallback) -> finding later.
            row["description"] = f"{row.get('name', 'Organization')}. Description not available."
            fixes.append(Fix("organization", row["id"], "organization_description_missing",
                             "fallback description; verifier should draft one", needs_person=True))

    # 3. Service status missing (ODRS) -> active, never inferred defunct (§14.4).
    for row in rows.get("service", {}).values():
        if is_missing(row.get("status")):
            row["status"] = "active"
            fixes.append(Fix("service", row["id"], "status_defaulted_active", service_ids=[row["id"]]))

    # 4. Phones: non-numbers become URLs; types normalised; duplicates dropped.
    seen_numbers: set[tuple] = set()
    for pid, row in list(rows.get("phone", {}).items()):
        number = str(row.get("number") or "").strip()
        owner = next(((c, row[c]) for c in ("service_id", "organization_id", "location_id", "contact_id",
                                            "service_at_location_id") if not is_missing(row.get(c))), ("", ""))
        if not _PHONE_DIGITS.search(number) and _DOMAINISH.match(number):
            url = number if number.startswith("http") else f"https://{number}"
            uid = str(uuid.uuid5(_NS, f"{source}|url|{pid}"))
            link = {"id": uid, "url": url, "label": row.get("description") or "Website"}
            if owner[0] in ("service_id", "organization_id"):
                link[owner[0]] = owner[1]
            rows["url"][uid] = link
            del rows["phone"][pid]
            fixes.append(Fix("phone", pid, "phone_was_url", url, service_ids=[owner[1]] if owner[0] == "service_id" else []))
            continue
        digits = re.sub(r"\D", "", number)
        key = (owner, digits[-10:])
        if digits and key in seen_numbers:
            del rows["phone"][pid]
            fixes.append(Fix("phone", pid, "duplicate_phone_dropped"))
            continue
        seen_numbers.add(key)
        mapped = _PHONE_TYPE.get(str(row.get("type") or "").lower(), "voice")
        if row.get("type") != mapped:
            row["type"] = mapped

    # 5a. ODRS addresses carry no country / address_type: ODRS is a US-only
    #     directory of physical service sites (tech doc §4), so derive both.
    for aid, row in rows.get("address", {}).items():
        if is_missing(row.get("country")) and _US_STATE.match(str(row.get("state_province") or "")):
            row["country"] = "US"
            fixes.append(Fix("address", aid, "country_from_us_state"))
        if is_missing(row.get("address_type")):
            row["address_type"] = "physical"
            fixes.append(Fix("address", aid, "address_type_defaulted_physical"))

    # 5b. Addresses HSDS can't hold (no city / ZIP, e.g. NDP "Various Locations").
    required = ("address_1", "city", "state_province", "postal_code", "country")
    dropped_at = set()  # locations that had a (partial) street address: still physical places
    for aid, row in list(rows.get("address", {}).items()):
        missing = [c for c in required if is_missing(row.get(c))]
        if not missing:
            continue
        dropped_at.add(row.get("location_id"))
        loc = rows.get("location", {}).get(row.get("location_id"))
        del rows["address"][aid]
        place = row.get("region") or row.get("state_province") or ""
        if loc is not None and is_missing(loc.get("description")):
            name = str(loc.get("name") or "")
            loc["description"] = (f"{name} ({place})" if place and name else name or place or "Location without a street address")
        fixes.append(Fix("address", aid, "address_incomplete_dropped", f"missing {', '.join(missing)}",
                         service_ids=_services_at(ds, row.get("location_id")), needs_person=True))

    # 5c. location_type from what the location is: a street address or map
    #     point -> physical; a URL only -> virtual. Otherwise left for validation.
    for lid, loc in rows.get("location", {}).items():
        if not is_missing(loc.get("location_type")):
            continue
        has_addr = any(a.get("location_id") == lid for a in rows.get("address", {}).values())
        if has_addr or lid in dropped_at or not is_missing(loc.get("latitude")):
            loc["location_type"] = "physical"
        elif not is_missing(loc.get("url")):
            loc["location_type"] = "virtual"
        else:
            continue
        fixes.append(Fix("location", lid, "location_type_derived", loc["location_type"]))

    # 6. Hours only as text ("2nd Sunday of the month 1:30pm-3pm") -> RFC 5545 rows.
    from .schedules import parse_text
    for sid, row in list(rows.get("schedule", {}).items()):
        if not is_missing(row.get("opens_at")) or is_missing(row.get("description")):
            continue
        parsed = parse_text(str(row["description"]))
        owner = {c: row[c] for c in ("service_id", "location_id", "service_at_location_id") if not is_missing(row.get(c))}
        if parsed and all(p.get("closes_at") for p in parsed):
            del rows["schedule"][sid]
            for i, p in enumerate(parsed):
                nid = sid if i == 0 else str(uuid.uuid5(_NS, f"{source}|schedule|{sid}|{i}"))
                rows["schedule"][nid] = {"id": nid, **owner, **p}
            fixes.append(Fix("schedule", sid, "schedule_parsed_from_text", str(row["description"])[:120]))
        else:
            fixes.append(Fix("schedule", sid, "schedule_text_only", str(row["description"])[:120],
                             service_ids=[owner["service_id"]] if "service_id" in owner else [], needs_person=True))

    # 7. Links that point nowhere.
    for sid, row in list(rows.get("service_at_location", {}).items()):
        if row.get("service_id") not in rows.get("service", {}) or row.get("location_id") not in rows.get("location", {}):
            del rows["service_at_location"][sid]
            fixes.append(Fix("service_at_location", sid, "invalid_link"))
    return fixes


def _services_at(ds: Dataset, location_id) -> list[str]:
    return [s["service_id"] for s in ds.rows.get("service_at_location", {}).values()
            if s.get("location_id") == location_id and s.get("service_id")]
