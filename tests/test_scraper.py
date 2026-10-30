"""Tests for BoostApp scraper."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
import sqlalchemy as sa

from scraper import (
    ClassItem, extract_studio_id, parse_schedule_html, fetch_schedule,
)
from db import get_engine, init_db, SessionLocal
from models import ScrapeCache


FIXTURE = Path(__file__).parent / "fixtures" / "boost_week_2026-02-02.html"


@pytest.fixture
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def session():
    eng = get_engine("sqlite:///:memory:")
    init_db(eng)
    SessionLocal.configure(bind=eng)
    with SessionLocal() as s:
        yield s


# --- extract_studio_id ---

def test_extract_studio_id_from_url():
    url = "https://app.boostapp.co.il/Rest.php?datepicker=02/02&StudioUrl=636a5a359ccfa"
    assert extract_studio_id(url) == "636a5a359ccfa"


def test_extract_studio_id_from_bare_id():
    assert extract_studio_id("636a5a359ccfa") == "636a5a359ccfa"


def test_extract_studio_id_rejects_empty():
    with pytest.raises(ValueError):
        extract_studio_id("")


def test_extract_studio_id_rejects_url_without_studio_url():
    with pytest.raises(ValueError):
        extract_studio_id("https://app.boostapp.co.il/Rest.php?datepicker=02/02")


# --- parse_schedule_html ---

def test_parse_schedule_counts_days(html):
    items = parse_schedule_html(html)
    dates = {c.date for c in items}
    assert len(dates) == 8
    assert dt.date(2026, 2, 2) in dates
    assert dt.date(2026, 2, 9) in dates


def test_parse_schedule_first_class_monday_6am(html):
    items = parse_schedule_html(html)
    monday_6am = [c for c in items
                  if c.date == dt.date(2026, 2, 2) and c.time == dt.time(6, 0)]
    body_pump = [c for c in monday_6am if c.class_type == "Body Pump"]
    assert len(body_pump) == 1
    c = body_pump[0]
    assert c.trainer == "אור כץ"
    assert c.room == "POWER"


def test_parse_schedule_trainer_name_trimmed(html):
    """Review Focus #1 — Hebrew whitespace artifacts."""
    items = parse_schedule_html(html)
    assert len(items) > 0
    for c in items:
        assert c.trainer == c.trainer.strip()
        assert "\t" not in c.trainer
        assert " " not in c.trainer
        assert "  " not in c.trainer  # no double space


def test_parse_schedule_handles_h5_with_anchor(html):
    """Classes whose <h5> wraps <a class='ClickClass'> still yield plain class text."""
    items = parse_schedule_html(html)
    # 09:35 Monday has an anchor-wrapped Body Pump by דריה מרקולוב
    hits = [c for c in items
            if c.date == dt.date(2026, 2, 2) and c.time == dt.time(9, 35)
            and c.trainer.startswith("דריה")]
    assert len(hits) == 1
    assert hits[0].class_type == "Body Pump"
    assert "<" not in hits[0].class_type


def test_parse_schedule_empty_week_returns_empty():
    """Review Focus #2 — holiday week with no classes."""
    assert parse_schedule_html("<html><body>no schedule</body></html>") == []


# --- fetch_schedule ---

def _make_http_stub(html_text: str, raises: Exception | None = None):
    http = MagicMock(spec=requests.Session)
    resp = MagicMock()
    resp.text = html_text
    resp.encoding = "utf-8"
    resp.raise_for_status = MagicMock()
    if raises is not None:
        http.get.side_effect = raises
    else:
        http.get.return_value = resp
    return http


def test_fetch_schedule_uses_cache_within_ttl(session, html):
    now = dt.datetime(2026, 2, 2, 12, 0, tzinfo=dt.timezone.utc)
    http = _make_http_stub(html)
    first = fetch_schedule("636a5a359ccfa", weeks_ahead=0,
                           session=session, now=now, http=http,
                           cache_ttl_hours=1)
    assert len(first) > 0
    first_call_count = http.get.call_count
    assert first_call_count >= 1

    http.get.reset_mock()
    second = fetch_schedule("636a5a359ccfa", weeks_ahead=0,
                            session=session, now=now, http=http,
                            cache_ttl_hours=1)
    assert http.get.call_count == 0
    assert len(second) == len(first)


def test_fetch_schedule_stale_cache_on_network_failure(session, html):
    now = dt.datetime(2026, 2, 2, 12, 0, tzinfo=dt.timezone.utc)
    good_http = _make_http_stub(html)
    fetch_schedule("636a5a359ccfa", weeks_ahead=0,
                   session=session, now=now, http=good_http, cache_ttl_hours=1)

    later = now + dt.timedelta(hours=10)
    bad_http = _make_http_stub("", raises=requests.ConnectionError("down"))
    items = fetch_schedule("636a5a359ccfa", weeks_ahead=0,
                           session=session, now=later, http=bad_http,
                           cache_ttl_hours=1)
    assert len(items) > 0
    assert all(c.source == "cache-stale" for c in items)


def test_fetch_schedule_dedup_across_weeks(session, html):
    now = dt.datetime(2026, 2, 2, 12, 0, tzinfo=dt.timezone.utc)
    http = _make_http_stub(html)
    items = fetch_schedule("636a5a359ccfa", weeks_ahead=1,
                           session=session, now=now, http=http,
                           cache_ttl_hours=1)
    # Each (date,time,trainer,class_type,room) tuple appears at most once.
    keys = [(c.date, c.time, c.trainer, c.class_type, c.room) for c in items]
    assert len(keys) == len(set(keys))
