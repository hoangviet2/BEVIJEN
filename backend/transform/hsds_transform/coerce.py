"""Normalise mapped values to the HSDS column types and controlled vocabularies.

A value that cannot be converted is kept as received (never silently dropped) and
is reported by the validator.
"""
from __future__ import annotations

import json
import re

from . import schema, vocab
from .mapper import Dataset, is_missing

_NUM = re.compile(r"^[-+]?\d+(?:\.\d+)?$")
_EXT = re.compile(r"\s*(?:x|ext\.?|extension|#)\s*(\d+)\s*$", re.I)
_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[T ].*)?$")
_TIME = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?(?::\d{2})?\s*([ap]\.?m\.?)?\s*([+-]\d{2}:?\d{2}|Z)?\s*$", re.I)
_DATE_COLUMNS = {"assured_date", "valid_from", "valid_to", "dtstart", "until", "last_action_date"}


def _text(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (list, tuple)):
        return "; ".join(_text(v) for v in value if not is_missing(v))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _number(value, integer_hint=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) if isinstance(value, float) and value.is_integer() and integer_hint else value
    text = str(value).strip().replace(",", "").lstrip("$")
    m = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not m or (not _NUM.match(text) and not integer_hint):
        return value  # left as-is; the validator flags it
    num = float(m.group(0))
    return int(num) if num.is_integer() else num


def _time(value) -> str:
    text = _text(value)
    m = _TIME.match(text)
    if not m:
        return text
    hour, minute, ap, offset = m.groups()
    hour = int(hour)
    if ap:
        ap = ap.lower().replace(".", "")
        if ap == "pm" and hour < 12:
            hour += 12
        if ap == "am" and hour == 12:
            hour = 0
    out = f"{hour:02d}:{int(minute or 0):02d}"
    if offset:
        out += offset if offset == "Z" or ":" in offset else f"{offset[:3]}:{offset[3:]}"
    return out


def _byday(value) -> str:
    text = _text(value)
    codes = []
    for part in re.split(r"[,;/ ]+|\band\b", text):
        p = part.strip().lower().rstrip(".")
        if not p:
            continue
        if "-" in p or "–" in p:  # ranges like mon-fri
            a, _, b = re.split(r"(-|–)", p, maxsplit=1)
            a, b = vocab.DAYS.get(a.strip()), vocab.DAYS.get(b.strip())
            if a and b:
                i, j = vocab.DAY_ORDER.index(a), vocab.DAY_ORDER.index(b)
                codes.extend(vocab.DAY_ORDER[i:j + 1] if i <= j else vocab.DAY_ORDER[i:] + vocab.DAY_ORDER[:j + 1])
                continue
            return text
        code = vocab.DAYS.get(p) or (p.upper() if p.upper() in vocab.DAY_ORDER else None)
        if not code:
            return text
        codes.append(code)
    return ",".join(dict.fromkeys(codes)) if codes else text


def _url(value) -> str:
    text = _text(value)
    if re.match(r"^(www\.|[a-z0-9-]+\.[a-z]{2,}(/|$))", text, re.I) and "@" not in text and " " not in text:
        return "http://" + text
    return text


def coerce_value(table: str, column: str, value):
    col = schema.table(table).columns[column]
    if col.type in ("number", "integer"):
        return _number(value, integer_hint=column in ("year_incorporated", "minimum_age", "maximum_age",
                                                      "extension", "count", "interval"))
    text = _text(value)
    if col.format == "email":
        return text.removeprefix("mailto:").strip()
    if col.format == "uri":
        return _url(text)
    if col.format == "date-time":
        m = _DATE.match(text)
        if m and len(text) == 10:
            return f"{text}T00:00:00Z"
        return text
    if column in _DATE_COLUMNS:
        m = _DATE.match(text)
        return m.group(1) if m else text
    if column in ("opens_at", "closes_at"):
        return _time(text)
    if table == "schedule" and column == "byday":
        return _byday(text)
    if col.enum:
        low = text.lower()
        if column == "status":
            return vocab.SERVICE_STATUS.get(low, text)
        if column in ("location_type", "address_type"):
            return vocab.LOCATION_TYPES.get(low, text)
        if column in ("freq", "wkst"):
            up = text.upper()
            return vocab.DAYS.get(low, up) if column == "wkst" else up
    if table == "address" and column == "country":
        return vocab.COUNTRY_CODES.get(text.lower(), text.upper() if len(text) == 2 else text)
    return text


def coerce_dataset(ds: Dataset) -> None:
    for table, rows in ds.rows.items():
        cols = schema.table(table).columns
        for row in rows.values():
            for column in list(row):
                if column not in cols:
                    continue
                if is_missing(row[column]):
                    row.pop(column)
                    continue
                if column == "id" or column.endswith("_id"):
                    row[column] = str(row[column])
                    continue
                row[column] = coerce_value(table, column, row[column])
            _row_fixes(table, row)
    _infer_location_types(ds)


def _infer_location_types(ds: Dataset) -> None:
    """location_type is required. When the source gives none, derive it from the
    location's own addresses (physical address -> physical, only postal -> postal).
    Locations without addresses keep NA."""
    types: dict[str, set] = {}
    for addr in ds.rows.get("address", {}).values():
        if addr.get("location_id") and addr.get("address_type"):
            types.setdefault(addr["location_id"], set()).add(addr["address_type"])
    for lid, loc in ds.rows.get("location", {}).items():
        if "location_type" in loc:
            continue
        found = types.get(lid, set())
        if "physical" in found:
            loc["location_type"] = "physical"
        elif "postal" in found:
            loc["location_type"] = "postal"


def _row_fixes(table: str, row: dict) -> None:
    if table == "phone":
        number = row.get("number")
        if isinstance(number, str):
            m = _EXT.search(number)
            if m and "extension" not in row:
                row["extension"] = int(m.group(1))
                row["number"] = number[: m.start()].strip()
        ptype = row.get("type")
        if isinstance(ptype, str):
            mapped = vocab.PHONE_TYPES.get(ptype.strip().lower())
            if mapped:
                row["type"] = mapped
            elif "description" not in row:  # e.g. "Main Office" is a description, not a type
                row["description"] = ptype
                row.pop("type")
    elif table == "address":
        state = row.get("state_province")
        country = row.get("country")
        if isinstance(state, str) and (country in (None, "US")):
            row["state_province"] = vocab.US_STATES.get(state.lower(), state)
