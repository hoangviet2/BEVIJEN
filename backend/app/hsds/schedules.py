"""Schedules: free text -> RFC 5545 rows, and local times -> times with UTC offset.

`parse_text` is deterministic and handles the patterns in SKILL.md §4. Text it
can't parse returns no rows (the description is kept); the `parse_schedule`
agent tool may then ask Bedrock and re-check the answer with `check_rows`.

`with_offsets` renders `opens_at`/`closes_at` with the offset that applies on
the response date in `RE_LOCAL_TZ` (design §14.3) and fills the numeric
`timezone` (UTC offset in hours, e.g. -8).
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

_DAYS = {"mon": "MO", "tue": "TU", "wed": "WE", "thu": "TH", "fri": "FR", "sat": "SA", "sun": "SU"}
_ORD = {"1st": 1, "first": 1, "2nd": 2, "second": 2, "3rd": 3, "third": 3, "4th": 4, "fourth": 4,
        "5th": 5, "fifth": 5, "last": -1}
_DAY_RE = r"\b(mon|tue|wed|thu|fri|sat|sun)(?:day|s|sday|nesday|rsday|urday|r|rs)?\b\.?"
_TIME_RE = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?"
_RANGE = re.compile(_TIME_RE + r"\s*(?:-|–|to)\s*" + _TIME_RE, re.I)
_SINGLE = re.compile(_TIME_RE, re.I)
_VALID_BYDAY = re.compile(r"^(-?[1-5]?(MO|TU|WE|TH|FR|SA|SU))(,-?[1-5]?(MO|TU|WE|TH|FR|SA|SU))*$")


def _hhmm(h: str, m: str | None, ap: str | None, fallback_ap: str | None = None) -> str | None:
    hour, minute = int(h), int(m or 0)
    ap = (ap or fallback_ap or "").lower().replace(".", "")
    if ap == "pm" and hour < 12:
        hour += 12
    if ap == "am" and hour == 12:
        hour = 0
    if not ap and hour < 7:  # "9-5" style: small numbers are afternoon
        hour += 12
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _times(text: str) -> tuple[str | None, str | None]:
    m = _RANGE.search(text)
    if m:
        closes = _hhmm(m.group(4), m.group(5), m.group(6))
        opens = _hhmm(m.group(1), m.group(2), m.group(3), fallback_ap=m.group(6) if not m.group(3) else None)
        if opens and closes and opens > closes and not m.group(3):
            opens = _hhmm(m.group(1), m.group(2), "am")
        return opens, closes
    m = _SINGLE.search(text)
    if m and (m.group(3) or m.group(2)):
        return _hhmm(m.group(1), m.group(2), m.group(3)), None
    return None, None


def _day_list(text: str) -> list[str]:
    t = text.lower()
    rng = re.search(_DAY_RE + r"\s*(?:-|–|through|to)\s*" + _DAY_RE, t)
    order = list(_DAYS.values())
    if rng:
        a, b = _DAYS[rng.group(1)], _DAYS[rng.group(2)]
        i, j = order.index(a), order.index(b)
        return order[i:j + 1] if i <= j else order[i:] + order[:j + 1]
    if "weekday" in t:
        return order[:5]
    return [_DAYS[d] for d in re.findall(_DAY_RE, t)]


def parse_text(text: str) -> list[dict]:
    """Return HSDS schedule rows (without ids) for text; [] when unsure."""
    if not text or re.search(r"call for|by appointment|varies", text, re.I):
        return []
    rows: list[dict] = []
    for segment in [s for s in re.split(r"[;\n/]+", text) if s.strip()]:
        seg = segment.strip()
        opens, closes = _times(seg)
        ords = re.findall(r"\b(1st|2nd|3rd|4th|5th|first|second|third|fourth|fifth|last)\b", seg, re.I)
        days = _day_list(seg)
        if not days or not opens:
            return []  # one unparsed segment -> don't guess any
        row: dict[str, Any] = {"description": text.strip(), "opens_at": opens}
        if closes:
            row["closes_at"] = closes
        if ords and ("month" in seg.lower() or len(days) == 1):
            row["freq"] = "MONTHLY"
            row["byday"] = ",".join(f"{_ORD[o.lower()]}{d}" for d in days for o in ords)
        else:
            row["freq"] = "WEEKLY"
            row["byday"] = ",".join(days)
        rows.append(row)
    return rows


def check_rows(rows: list[dict]) -> list[str]:
    """Deterministic sanity check for model-proposed rows."""
    problems = []
    for i, r in enumerate(rows):
        if r.get("freq") not in ("WEEKLY", "MONTHLY", "DAILY", "YEARLY"):
            problems.append(f"row {i}: bad freq")
        if r.get("byday") and not _VALID_BYDAY.match(str(r["byday"])):
            problems.append(f"row {i}: bad byday")
        for col in ("opens_at", "closes_at"):
            if r.get(col) and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(r[col])):
                problems.append(f"row {i}: bad {col}")
        if r.get("opens_at") and r.get("closes_at") and r["opens_at"] >= r["closes_at"]:
            problems.append(f"row {i}: closes before it opens")
    return problems


def _offset_minutes(tz: ZoneInfo, on: date, hhmm: str) -> int:
    h, m = (int(x) for x in hhmm[:5].split(":"))
    off = datetime.combine(on, time(h, m), tz).utcoffset()
    return int(off.total_seconds() // 60) if off else 0


def _offset(tz: ZoneInfo, on: date, hhmm: str) -> str:
    total = _offset_minutes(tz, on, hhmm)
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"{hhmm[:5]}{sign}{total // 60:02d}:{total % 60:02d}"


def with_offsets(doc: Any, tz_name: str, on: date | None = None) -> Any:
    """Return a copy of an HSDS document with offsets on every schedule time."""
    tz = ZoneInfo(tz_name)
    on = on or datetime.now(tz).date()

    def walk(node: Any, key: str = "") -> Any:
        if isinstance(node, list):
            return [walk(n, key) for n in node]
        if not isinstance(node, dict):
            return node
        out = {k: walk(v, k) for k, v in node.items()}
        if key == "schedules":
            for col in ("opens_at", "closes_at"):
                val = out.get(col)
                if isinstance(val, str) and re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", val):
                    if "timezone" not in out:  # HSDS timezone = numeric UTC offset in hours
                        out["timezone"] = _offset_minutes(tz, on, val) / 60
                    out[col] = _offset(tz, on, val)
        return out

    return walk(doc)
