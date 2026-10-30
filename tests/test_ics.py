"""Tests for ICS generator and UID helpers."""
from __future__ import annotations

import datetime as dt

import pytest
from icalendar import Calendar

from calendar_ics import build_ics, strict_uid, loose_uid
from scraper import ClassItem


def _c(date=dt.date(2026, 2, 2), time=dt.time(18, 0),
       trainer="שירן אמויאל", class_type="Body Pump", room="POWER"):
    return ClassItem(date=date, time=time, trainer=trainer,
                     class_type=class_type, room=room, source="live")


def test_strict_uid_stable():
    c = _c()
    uid1 = strict_uid(c)
    uid2 = strict_uid(c)
    assert uid1 == uid2
    assert uid1.endswith("@studio-calendar")


def test_loose_uid_ignores_time_and_room():
    a = _c(time=dt.time(18, 0), room="POWER")
    b = _c(time=dt.time(19, 0), room="MOTION")
    assert loose_uid(a) == loose_uid(b)


def test_build_ics_contains_vtimezone_jerusalem():
    out = build_ics([_c()])
    assert b"BEGIN:VTIMEZONE" in out
    assert b"TZID:Asia/Jerusalem" in out


def test_build_ics_hebrew_round_trip():
    out = build_ics([_c()])
    cal = Calendar.from_ical(out)
    vevents = [c for c in cal.walk("VEVENT")]
    assert len(vevents) == 1
    assert str(vevents[0]["SUMMARY"]) == "Body Pump — שירן אמויאל"


def test_build_ics_event_summary_location_description():
    out = build_ics([_c()])
    cal = Calendar.from_ical(out)
    v = next(iter(cal.walk("VEVENT")))
    assert str(v["SUMMARY"]) == "Body Pump — שירן אמויאל"
    assert str(v["LOCATION"]) == "סטודיו נופר — POWER"
    assert str(v["DESCRIPTION"]) == "Body Pump | שירן אמויאל | אולם POWER"


def test_build_ics_duration_55_minutes():
    out = build_ics([_c()], duration_minutes=55)
    cal = Calendar.from_ical(out)
    v = next(iter(cal.walk("VEVENT")))
    start = v["DTSTART"].dt
    end = v["DTEND"].dt
    assert (end - start) == dt.timedelta(minutes=55)


def test_build_ics_dst_transition():
    """Review Focus #5 — DST transition.

    Israel DST 2026 starts 2026-03-27 (Friday 02:00 → 03:00). A class on
    2026-03-28 (Saturday) 18:00 Asia/Jerusalem is in IDT (+03:00).
    """
    c = _c(date=dt.date(2026, 3, 28), time=dt.time(18, 0))
    out = build_ics([c])
    # Verify VEVENT uses TZID=Asia/Jerusalem on DTSTART (not fixed offset).
    assert b"DTSTART;TZID=Asia/Jerusalem:20260328T180000" in out
    # Verify parsed instant resolves to UTC+3.
    cal = Calendar.from_ical(out)
    v = next(iter(cal.walk("VEVENT")))
    offset = v["DTSTART"].dt.utcoffset()
    assert offset == dt.timedelta(hours=3)
