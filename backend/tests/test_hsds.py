"""HSDS helpers: schema validation, schedules, offsets, row/item round trip."""

from datetime import date

from app.hsds.render import items_to_rows, render_service, rows_to_items
from app.hsds.schedules import check_rows, parse_text, with_offsets
from app.hsds.validate import validate_object, validate_service

from conftest import handoff_record


def test_schema_rejects_missing_required():
    assert not validate_object("organization", {"id": "00000000-0000-0000-0000-000000000001", "name": "x"}).valid
    assert validate_object("organization", {"id": "00000000-0000-0000-0000-000000000001", "name": "x",
                                            "description": "y"}).valid


def test_service_needs_organization():
    rec = handoff_record()
    rec.pop("organization")
    assert not validate_service(rec).valid


def test_parse_weekly_and_monthly():
    assert parse_text("Mon-Fri 9am-5pm") == [{"description": "Mon-Fri 9am-5pm", "opens_at": "09:00",
                                               "closes_at": "17:00", "freq": "WEEKLY", "byday": "MO,TU,WE,TH,FR"}]
    r = parse_text("2nd Sunday of the month 1:30pm-3pm")[0]
    assert (r["freq"], r["byday"], r["opens_at"], r["closes_at"]) == ("MONTHLY", "2SU", "13:30", "15:00")
    r = parse_text("2nd and 4th Wednesday 10-11am")[0]
    assert r["byday"] == "2WE,4WE" and r["opens_at"] == "10:00"


def test_parse_refuses_to_guess():
    assert parse_text("Call for schedule") == []
    assert parse_text("Mon-Fri 9-5; whenever") == []
    assert parse_text("Wednesday 6:30pm")[0].get("closes_at") is None  # start only -> no close time


def test_check_rows_catches_bad_model_output():
    assert check_rows([{"freq": "WEEKLY", "byday": "MO", "opens_at": "17:00", "closes_at": "09:00"}])
    assert check_rows([{"freq": "HOURLY"}])
    assert not check_rows([{"freq": "MONTHLY", "byday": "2SU,-1FR", "opens_at": "09:00", "closes_at": "10:00"}])


def test_offsets_follow_daylight_saving():
    doc = {"schedules": [{"opens_at": "09:00", "closes_at": "17:00"}]}
    winter = with_offsets(doc, "America/Los_Angeles", date(2026, 1, 15))["schedules"][0]
    summer = with_offsets(doc, "America/Los_Angeles", date(2026, 7, 15))["schedules"][0]
    assert winter["opens_at"] == "09:00-08:00" and winter["timezone"] == -8
    assert summer["opens_at"] == "09:00-07:00" and summer["timezone"] == -7
    # DST change days: 2026-03-08 and 2026-11-01
    assert with_offsets(doc, "America/Los_Angeles", date(2026, 3, 8))["schedules"][0]["opens_at"].endswith("-07:00")
    assert with_offsets(doc, "America/Los_Angeles", date(2026, 11, 1))["schedules"][0]["opens_at"].endswith("-08:00")


def test_rows_items_round_trip():
    from app.records import flatten
    rows = flatten(handoff_record(complete=True), "test")
    items = rows_to_items(rows, source="test", actor="source:test", now="2026-11-04T00:00:00Z")
    assert all(i["PK"].split("#")[0] == i["entity_type"].upper() for i in items)
    loc = next(i for i in items if i["entity_type"] == "location")
    assert loc["zip_pk"] == "ZIP#94103"
    assert all(i["org_pk"] in ("SHARED", "ORGANIZATION#00000000-0000-0000-0000-0000000000a1") for i in items)
    back = items_to_rows(items)
    sid = next(iter(rows["service"]))
    assert render_service(sid, back) == render_service(sid, rows)
