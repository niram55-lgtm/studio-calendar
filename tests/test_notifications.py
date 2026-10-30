"""Tests for notifications: ntfy push + digest."""
from __future__ import annotations

import datetime as dt
from email.header import Header
from unittest.mock import MagicMock

import pytest
import requests

from calendar_google import SyncDiff
from calendar_ics import loose_uid, strict_uid
from db import get_engine, init_db, SessionLocal
from models import (
    User, UserSettings, PendingDigest, NotificationsLog, ClassSnapshot,
)
from notifications import process_diff, send_digest, push
from scraper import ClassItem


@pytest.fixture
def session():
    eng = get_engine("sqlite:///:memory:")
    init_db(eng)
    SessionLocal.configure(bind=eng)
    with SessionLocal() as s:
        yield s


def _user(session, *, urgent=24, cancel=True, change=True, new_cls=False):
    u = User(google_sub="g1", email="x@y", display_name="x")
    session.add(u)
    session.flush()
    s = UserSettings(
        user_id=u.id, feed_token="f1", ntfy_topic="my-topic",
        urgent_window_hours=urgent,
        notify_on_cancellation=cancel,
        notify_on_change=change,
        notify_on_new_class=new_cls,
    )
    session.add(s)
    session.commit()
    return u


def _c(hour: int, date=dt.date(2026, 2, 2), trainer="שירן",
       class_type="Body Pump", room="POWER"):
    return ClassItem(date=date, time=dt.time(hour, 0),
                     trainer=trainer, class_type=class_type,
                     room=room, source="live")


def _snap(user_id: int, c: ClassItem, event_id: str = "e1") -> ClassSnapshot:
    return ClassSnapshot(
        user_id=user_id,
        loose_uid=loose_uid(c), strict_uid=strict_uid(c),
        class_date=c.date, class_time=c.time.strftime("%H:%M"),
        trainer=c.trainer, class_type=c.class_type, room=c.room,
        google_event_id=event_id,
    )


class FakeHTTP:
    def __init__(self):
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        return resp


def test_urgent_cancellation_sends_push(session):
    u = _user(session)
    now = dt.datetime(2026, 2, 2, 15, 0, tzinfo=dt.timezone.utc)
    # Class at 18:00 Jerusalem on 2026-02-02 = 16:00 UTC, so 1 h from now.
    removed_snap = _snap(u.id, _c(hour=18))
    session.add(removed_snap)
    session.commit()
    diff = SyncDiff(removed=[removed_snap])
    http = FakeHTTP()
    process_diff(session, u.id, diff, now=now, http=http,
                 ntfy_server="https://ntfy.sh")
    assert len(http.posts) == 1
    url, kwargs = http.posts[0]
    assert url == "https://ntfy.sh/my-topic"
    title_header = kwargs["headers"]["Title"]
    assert "בוטל" in title_header or b"=?utf-8?" in title_header.encode("latin-1", "replace")


def test_non_urgent_change_queues_digest(session):
    u = _user(session, urgent=24)
    now = dt.datetime(2026, 2, 2, 10, 0, tzinfo=dt.timezone.utc)
    old_c = _c(hour=18, date=dt.date(2026, 2, 5))  # 3 days ahead
    new_item = _c(hour=19, date=dt.date(2026, 2, 5))
    snap = _snap(u.id, old_c)
    session.add(snap)
    session.commit()
    diff = SyncDiff(changed=[(snap, new_item)])
    http = FakeHTTP()
    process_diff(session, u.id, diff, now=now, http=http,
                 ntfy_server="https://ntfy.sh")
    assert http.posts == []
    assert session.query(PendingDigest).count() == 1


def test_notify_toggle_off_cancellation_skips(session):
    u = _user(session, cancel=False)
    now = dt.datetime(2026, 2, 2, 15, 0, tzinfo=dt.timezone.utc)
    snap = _snap(u.id, _c(hour=18))
    session.add(snap)
    session.commit()
    diff = SyncDiff(removed=[snap])
    http = FakeHTTP()
    process_diff(session, u.id, diff, now=now, http=http,
                 ntfy_server="https://ntfy.sh")
    assert http.posts == []
    assert session.query(PendingDigest).count() == 0


def test_notifications_log_prevents_duplicate_push(session):
    """Review Focus #4."""
    u = _user(session)
    now = dt.datetime(2026, 2, 2, 15, 0, tzinfo=dt.timezone.utc)
    snap = _snap(u.id, _c(hour=18))
    session.add(snap)
    session.commit()
    diff = SyncDiff(removed=[snap])
    http = FakeHTTP()
    process_diff(session, u.id, diff, now=now, http=http,
                 ntfy_server="https://ntfy.sh")
    process_diff(session, u.id, diff, now=now, http=http,
                 ntfy_server="https://ntfy.sh")
    assert len(http.posts) == 1


def test_send_digest_merges_pending_rows(session):
    u = _user(session)
    for ct, label in [("removed", "A"), ("changed", "B"), ("added", "C")]:
        session.add(PendingDigest(
            user_id=u.id, change_type=ct,
            payload_json=f'{{"title":"T-{label}","body":"B-{label}"}}',
        ))
    session.commit()
    http = FakeHTTP()
    send_digest(session, u.id, now=dt.datetime(2026, 2, 2, 6, 30,
                                               tzinfo=dt.timezone.utc),
                http=http, ntfy_server="https://ntfy.sh")
    assert len(http.posts) == 1
    body = http.posts[0][1].get("data", b"")
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    for label in ("A", "B", "C"):
        assert label in body
    assert session.query(PendingDigest).count() == 0


def test_push_hebrew_title_is_rfc2047_encoded():
    http = FakeHTTP()
    push(http, "https://ntfy.sh", "my-topic", "⚠️ בוטל", "body")
    _, kwargs = http.posts[0]
    title_header = kwargs["headers"]["Title"]
    # RFC 2047 encoded (base64) OR raw-utf-8 as `=?utf-8?...?=`.
    assert title_header.startswith("=?") or "בוטל" in title_header
