"""BoostApp HTML scraper → list[ClassItem]."""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from models import ScrapeCache

logger = logging.getLogger(__name__)

JERUSALEM = ZoneInfo("Asia/Jerusalem")
_STUDIO_ID_RE = re.compile(r"^[0-9a-f]{10,}$", re.IGNORECASE)
_DAY_RE = re.compile(r"יום \S+\s+(\d{2})/(\d{2})/(\d{4})")
_WS_RE = re.compile(r"[\s ]+")


@dataclass
class ClassItem:
    date: dt.date
    time: dt.time
    trainer: str
    class_type: str
    room: str
    source: Literal["live", "cache-fresh", "cache-stale"] = "live"


def extract_studio_id(link_or_id: str) -> str:
    if not link_or_id:
        raise ValueError("empty studio link")
    s = link_or_id.strip()
    if _STUDIO_ID_RE.match(s):
        return s
    parsed = urlparse(s)
    if not parsed.scheme:
        raise ValueError(f"not a URL and not a studio id: {link_or_id!r}")
    qs = parse_qs(parsed.query)
    vals = qs.get("StudioUrl")
    if not vals:
        raise ValueError("URL has no StudioUrl parameter")
    return vals[0]


def _clean(s: str) -> str:
    """Trim and collapse any run of whitespace/NBSP/tabs to one space."""
    return _WS_RE.sub(" ", s).strip()


def parse_schedule_html(html: str) -> list[ClassItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[ClassItem] = []

    # Each schedule day is a <table> whose <thead>/<h4> says "יום X DD/MM/YYYY".
    for h4 in soup.find_all("h4"):
        text = h4.get_text(" ", strip=True)
        m = _DAY_RE.search(text)
        if not m:
            continue
        dd, mm, yyyy = m.groups()
        try:
            day = dt.date(int(yyyy), int(mm), int(dd))
        except ValueError:
            continue

        # Walk the following <tbody> in the same table.
        table = h4.find_parent("table")
        if table is None:
            continue
        tbody = table.find("tbody")
        if tbody is None:
            continue

        for row in tbody.find_all("tr", recursive=False):
            classes_attr = row.get("class") or []
            if any(c.startswith("trAbout") or c.startswith("trGuide")
                   for c in classes_attr):
                continue
            tds = row.find_all("td", recursive=False)
            if len(tds) < 2:
                continue
            time_h5 = tds[0].find("h5")
            class_h5 = tds[1].find("h5")
            p = tds[1].find("p")
            if not (time_h5 and class_h5 and p):
                continue
            tm_text = _clean(time_h5.get_text(" ", strip=True))
            tm_match = re.match(r"^(\d{1,2}):(\d{2})$", tm_text)
            if not tm_match:
                continue
            tm = dt.time(int(tm_match.group(1)), int(tm_match.group(2)))

            class_type = _clean(class_h5.get_text(" ", strip=True))

            # Trainer = text after fa-user icon; room = text after fa-map-marker.
            icons = p.find_all("i", class_="fa")
            trainer = ""
            room = ""
            for i_tag in icons:
                cls = i_tag.get("class") or []
                sib = i_tag.next_sibling
                piece = ""
                while sib is not None and getattr(sib, "name", None) != "i":
                    if hasattr(sib, "get_text"):
                        piece += sib.get_text(" ", strip=False)
                    else:
                        piece += str(sib)
                    sib = sib.next_sibling
                piece = _clean(piece)
                if "fa-user" in cls:
                    trainer = piece
                elif "fa-map-marker" in cls:
                    room = piece

            if not trainer or not class_type:
                continue
            items.append(ClassItem(
                date=day, time=tm, trainer=trainer,
                class_type=class_type, room=room, source="live",
            ))

    return items


def _sunday_of_week(d: dt.date) -> dt.date:
    """Return the Sunday on or before d (Israeli week start)."""
    # Monday=0 ... Sunday=6; we want: shift back to Sunday.
    weekday = d.weekday()
    # Days since Sunday: Sunday=0, Monday=1, ..., Saturday=6
    days_since_sunday = (weekday + 1) % 7
    return d - dt.timedelta(days=days_since_sunday)


def _strict_key(c: ClassItem) -> tuple:
    return (c.date, c.time, c.trainer, c.class_type, c.room)


def _datepicker_for_week(week_start: dt.date) -> str:
    """BoostApp URL uses MM/DD; use the Sunday itself."""
    return week_start.strftime("%m/%d")


def fetch_schedule(
    studio_url: str,
    weeks_ahead: int = 1,
    *,
    session: Session,
    now: dt.datetime | None = None,
    http: requests.Session | None = None,
    cache_ttl_hours: int = 1,
    base_url: str = "https://app.boostapp.co.il/Rest.php",
    user_agent: str = "StudioCalendar/1.0",
    request_timeout: int = 15,
) -> list[ClassItem]:
    """Fetch current week + weeks_ahead weeks, caching per week_start."""
    http = http or requests.Session()
    now = now or dt.datetime.now(JERUSALEM)
    now_naive = now.astimezone(dt.timezone.utc).replace(tzinfo=None) \
        if now.tzinfo else now
    today = now.astimezone(JERUSALEM).date() if now.tzinfo else now.date()
    current_sunday = _sunday_of_week(today)

    all_items: list[ClassItem] = []
    stale_hit = False

    for w in range(weeks_ahead + 1):
        week_start = current_sunday + dt.timedelta(days=7 * w)
        cached = session.get(ScrapeCache, (studio_url, week_start))

        fresh_cache = (
            cached is not None
            and cached.fetched_at is not None
            and (now_naive - _as_naive_utc(cached.fetched_at))
            < dt.timedelta(hours=cache_ttl_hours)
        )

        html = None
        source: Literal["live", "cache-fresh", "cache-stale"] = "live"

        if fresh_cache:
            html = cached.html
            source = "cache-fresh"
        else:
            try:
                mmdd = _datepicker_for_week(week_start)
                resp = http.get(
                    base_url,
                    params={"datepicker": mmdd, "StudioUrl": studio_url},
                    headers={"User-Agent": user_agent},
                    timeout=request_timeout,
                )
                resp.raise_for_status()
                resp.encoding = "utf-8"
                html = resp.text
                source = "live"
                if cached is None:
                    session.add(ScrapeCache(
                        studio_url=studio_url, week_start=week_start,
                        html=html, fetched_at=now_naive,
                    ))
                else:
                    cached.html = html
                    cached.fetched_at = now_naive
                session.commit()
            except (requests.RequestException, Exception) as exc:
                if cached is not None:
                    html = cached.html
                    source = "cache-stale"
                    stale_hit = True
                    logger.warning("BoostApp fetch failed, serving stale cache: %s", exc)
                else:
                    logger.warning("BoostApp fetch failed, no cache: %s", exc)
                    continue

        for c in parse_schedule_html(html):
            c.source = source
            all_items.append(c)

    # Dedup by strict key, preserve first occurrence; sort by (date, time).
    seen: set[tuple] = set()
    deduped: list[ClassItem] = []
    for c in all_items:
        key = _strict_key(c)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    deduped.sort(key=lambda c: (c.date, c.time, c.trainer))
    return deduped


def _as_naive_utc(d: dt.datetime) -> dt.datetime:
    if d.tzinfo is None:
        return d
    return d.astimezone(dt.timezone.utc).replace(tzinfo=None)
