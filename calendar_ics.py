"""ICS (iCalendar) generator for ClassItem lists."""
from __future__ import annotations

import datetime as dt
import hashlib
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event, Timezone, TimezoneStandard, TimezoneDaylight

from scraper import ClassItem

JERUSALEM = ZoneInfo("Asia/Jerusalem")


def strict_uid(c: ClassItem) -> str:
    s = f"{c.date.isoformat()}|{c.time.isoformat()}|{c.trainer}|{c.class_type}|{c.room}"
    h = hashlib.sha1(s.encode("utf-8")).hexdigest()
    return f"{h}@studio-calendar"


def loose_uid(c: ClassItem) -> str:
    s = f"{c.date.isoformat()}|{c.trainer}|{c.class_type}"
    h = hashlib.sha1(s.encode("utf-8")).hexdigest()
    return f"{h}@studio-calendar"


def _jerusalem_vtimezone() -> Timezone:
    """VTIMEZONE for Asia/Jerusalem.

    Israel DST: starts the Friday before the last Sunday of March at 02:00
    (clocks go forward to 03:00). Ends the last Sunday of October at 02:00
    (clocks go back to 01:00). Standard = +02:00 (IST), Daylight = +03:00 (IDT).
    """
    tz = Timezone()
    tz.add("TZID", "Asia/Jerusalem")

    std = TimezoneStandard()
    std.add("DTSTART", dt.datetime(1970, 10, 25, 2, 0, 0))
    std.add("TZOFFSETFROM", dt.timedelta(hours=3))
    std.add("TZOFFSETTO", dt.timedelta(hours=2))
    std.add("TZNAME", "IST")
    std.add("RRULE", {"FREQ": "YEARLY", "BYMONTH": 10, "BYDAY": "-1SU"})

    dst = TimezoneDaylight()
    dst.add("DTSTART", dt.datetime(1970, 3, 27, 2, 0, 0))
    dst.add("TZOFFSETFROM", dt.timedelta(hours=2))
    dst.add("TZOFFSETTO", dt.timedelta(hours=3))
    dst.add("TZNAME", "IDT")
    # Israel DST starts the Friday BEFORE the last Sunday of March.
    # Last Sunday of March ∈ [25..31] → Friday-before ∈ [23..29].
    # BYDAY=FR + BYMONTHDAY picks the Friday in that window.
    dst.add("RRULE", {"FREQ": "YEARLY", "BYMONTH": 3,
                      "BYDAY": "FR",
                      "BYMONTHDAY": [23, 24, 25, 26, 27, 28, 29]})

    tz.add_component(std)
    tz.add_component(dst)
    return tz


def build_ics(
    classes: list[ClassItem],
    *,
    calendar_name: str = "סטודיו נופר",
    duration_minutes: int = 55,
) -> bytes:
    cal = Calendar()
    cal.add("prodid", "-//Studio Calendar//studio-calendar//HE")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", calendar_name)
    cal.add("x-wr-timezone", "Asia/Jerusalem")
    cal.add_component(_jerusalem_vtimezone())

    for c in classes:
        start = dt.datetime.combine(c.date, c.time, tzinfo=JERUSALEM)
        end = start + dt.timedelta(minutes=duration_minutes)
        ev = Event()
        ev.add("uid", strict_uid(c))
        ev.add("summary", f"{c.class_type} — {c.trainer}")
        ev.add("location", f"סטודיו נופר — {c.room}")
        ev.add("description",
               f"{c.class_type} | {c.trainer} | אולם {c.room}")
        ev.add("dtstart", start)
        ev.add("dtend", end)
        ev.add("dtstamp", dt.datetime.now(tz=dt.timezone.utc))
        cal.add_component(ev)

    return cal.to_ical()
