"""Tests for calendar_google.sync_user and ensure_calendar."""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import pytest
from google.auth.exceptions import RefreshError

from calendar_google import (
    SyncDiff, NeedsReauth, ensure_calendar, sync_user,
)
from calendar_ics import loose_uid, strict_uid
from db import get_engine, init_db, SessionLocal
from models import (
    User, UserSettings, GoogleToken, ClassSnapshot,
)
from scraper import ClassItem


@pytest.fixture
def session():
    eng = get_engine("sqlite:///:memory:")
    init_db(eng)
    SessionLocal.configure(bind=eng)
    with SessionLocal() as s:
        yield s


@pytest.fixture
def user(session):
    u = User(google_sub="g1", email="x@y", display_name="x")
    session.add(u)
    session.flush()
    session.add(UserSettings(user_id=u.id, feed_token="f1", ntfy_topic="n1"))
    session.add(GoogleToken(user_id=u.id, encrypted_access=b"a",
                            encrypted_refresh=b"b", expiry=dt.datetime.utcnow()))
    session.commit()
    return u


class FakeService:
    """Mimics the subset of googleapiclient we use."""
    def __init__(self):
        self.calls = []
        self.calendar_counter = 0
        self.event_counter = 0

    def calendars(self):
        parent = self
        class _Cals:
            def insert(self, body):
                parent.calendar_counter += 1
                parent.calls.append(("cal.insert", body))
                return _Exec({"id": f"calendar-{parent.calendar_counter}",
                              **body})
        return _Cals()

    def events(self):
        parent = self
        class _Evs:
            def insert(self, calendarId, body):
                parent.event_counter += 1
                eid = f"event-{parent.event_counter}"
                parent.calls.append(("insert", calendarId, body))
                return _Exec({"id": eid, **body})
            def update(self, calendarId, eventId, body):
                parent.calls.append(("update", calendarId, eventId, body))
                return _Exec({"id": eventId, **body})
            def delete(self, calendarId, eventId):
                parent.calls.append(("delete", calendarId, eventId))
                return _Exec(None)
        return _Evs()


class _Exec:
    def __init__(self, result):
        self._r = result
    def execute(self):
        return self._r


def _c(date=dt.date(2026, 2, 2), time=dt.time(18, 0),
       trainer="שירן", class_type="Body Pump", room="POWER") -> ClassItem:
    return ClassItem(date=date, time=time, trainer=trainer,
                     class_type=class_type, room=room, source="live")


def test_ensure_calendar_creates_once(session, user):
    fake = FakeService()
    cid = ensure_calendar(session, user.id, "סטודיו נופר",
                          build_service=lambda uid: fake)
    assert cid == "calendar-1"
    # Second call must not insert again.
    cid2 = ensure_calendar(session, user.id, "סטודיו נופר",
                           build_service=lambda uid: fake)
    assert cid2 == "calendar-1"
    assert fake.calendar_counter == 1


def test_sync_user_inserts_events_for_added(session, user):
    fake = FakeService()
    classes = [_c(class_type="A"), _c(class_type="B"), _c(class_type="C")]
    diff = sync_user(session, user.id, classes, duration_minutes=55,
                     build_service=lambda uid: fake)
    assert len(diff.added) == 3
    inserts = [c for c in fake.calls if c[0] == "insert"]
    assert len(inserts) == 3
    assert session.query(ClassSnapshot).filter_by(user_id=user.id).count() == 3


def test_sync_user_deletes_removed(session, user):
    fake = FakeService()
    old = _c(class_type="OldClass")
    snap = ClassSnapshot(
        user_id=user.id,
        loose_uid=loose_uid(old),
        strict_uid=strict_uid(old),
        class_date=old.date, class_time="18:00",
        trainer=old.trainer, class_type=old.class_type, room=old.room,
        google_event_id="event-old",
    )
    session.add(snap)
    session.commit()

    diff = sync_user(session, user.id, [], duration_minutes=55,
                     build_service=lambda uid: fake)
    assert len(diff.removed) == 1
    deletes = [c for c in fake.calls if c[0] == "delete"]
    assert ("delete", "calendar-1", "event-old") in deletes


def test_sync_user_updates_changed(session, user):
    fake = FakeService()
    old = _c(time=dt.time(18, 0))
    snap = ClassSnapshot(
        user_id=user.id,
        loose_uid=loose_uid(old),
        strict_uid=strict_uid(old),
        class_date=old.date, class_time="18:00",
        trainer=old.trainer, class_type=old.class_type, room=old.room,
        google_event_id="event-old",
    )
    session.add(snap)
    session.commit()

    new_item = _c(time=dt.time(19, 0))
    diff = sync_user(session, user.id, [new_item], duration_minutes=55,
                     build_service=lambda uid: fake)
    assert len(diff.changed) == 1
    updates = [c for c in fake.calls if c[0] == "update"]
    assert len(updates) == 1
    # Snapshot's class_time was updated.
    reloaded = session.query(ClassSnapshot).filter_by(user_id=user.id).one()
    assert reloaded.class_time == "19:00"


def test_sync_user_marks_needs_reauth_on_refresh_error(session, user):
    """Review Focus #3."""
    def _build(_uid):
        raise RefreshError("token invalid")
    with pytest.raises(NeedsReauth):
        sync_user(session, user.id, [_c()], duration_minutes=55,
                  build_service=_build)
    settings = session.query(UserSettings).filter_by(user_id=user.id).one()
    assert settings.last_sync_status == "needs_reauth"


def test_sync_user_empty_new_list_does_not_delete_everything_if_no_old(session, user):
    """Review Focus #2 — empty week leaves user alone."""
    fake = FakeService()
    diff = sync_user(session, user.id, [], duration_minutes=55,
                     build_service=lambda uid: fake)
    assert diff.added == [] and diff.removed == [] and diff.changed == []
    non_cal_calls = [c for c in fake.calls if c[0] in ("insert", "update", "delete")]
    assert non_cal_calls == []
    settings = session.query(UserSettings).filter_by(user_id=user.id).one()
    assert settings.last_sync_status == "ok"
