"""Tests for scheduler jobs."""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from calendar_google import NeedsReauth, SyncDiff
from config import load_config
from db import get_engine, init_db, SessionLocal
from models import User, UserSettings, GoogleToken
from scheduler import sync_all_users, start, send_all_digests


@pytest.fixture
def cfg():
    return load_config("config.example.yaml")


@pytest.fixture
def engine_and_factory():
    eng = get_engine("sqlite:///:memory:")
    init_db(eng)
    SessionLocal.configure(bind=eng)
    def factory():
        return SessionLocal()
    return eng, factory


def _mk_user(session, sub, studio_url: str | None, feed="f", ntfy="n"):
    u = User(google_sub=sub, email=f"{sub}@x", display_name=sub)
    session.add(u)
    session.flush()
    session.add(UserSettings(user_id=u.id, feed_token=f"{feed}{sub}",
                             ntfy_topic=f"{ntfy}{sub}", studio_url=studio_url))
    session.add(GoogleToken(user_id=u.id, encrypted_access=b"a",
                            encrypted_refresh=b"b",
                            expiry=dt.datetime.now(dt.timezone.utc)))
    return u


def test_sync_all_users_calls_sync_for_each_configured_user(cfg, engine_and_factory):
    _, factory = engine_and_factory
    with factory() as s:
        _mk_user(s, "u1", "studio1", feed="f1", ntfy="n1")
        _mk_user(s, "u2", "studio2", feed="f2", ntfy="n2")
        s.commit()

    calls = []
    def fake_fetch(studio_url, weeks_ahead, **kw):
        calls.append(("fetch", studio_url))
        return []
    def fake_sync(session, user_id, classes, duration_minutes, **kw):
        calls.append(("sync", user_id))
        return SyncDiff()
    def fake_process(session, user_id, diff, **kw):
        calls.append(("notify", user_id))

    with patch("scheduler.fetch_schedule", side_effect=fake_fetch), \
         patch("scheduler.sync_user", side_effect=fake_sync), \
         patch("scheduler.process_diff", side_effect=fake_process):
        sync_all_users(cfg, factory, http=MagicMock())
    fetches = [c for c in calls if c[0] == "fetch"]
    syncs = [c for c in calls if c[0] == "sync"]
    assert len(fetches) == 2
    assert len(syncs) == 2


def test_sync_all_users_skips_user_without_studio_url(cfg, engine_and_factory):
    _, factory = engine_and_factory
    with factory() as s:
        _mk_user(s, "u1", None, feed="f1", ntfy="n1")
        s.commit()
    fake_fetch = MagicMock(return_value=[])
    fake_sync = MagicMock(return_value=SyncDiff())
    with patch("scheduler.fetch_schedule", fake_fetch), \
         patch("scheduler.sync_user", fake_sync), \
         patch("scheduler.process_diff", MagicMock()):
        sync_all_users(cfg, factory, http=MagicMock())
    fake_fetch.assert_not_called()
    fake_sync.assert_not_called()


def test_sync_all_users_continues_after_one_user_fails(cfg, engine_and_factory):
    """Review Focus #3."""
    _, factory = engine_and_factory
    with factory() as s:
        _mk_user(s, "ua", "studio-a", feed="fa", ntfy="na")
        _mk_user(s, "ub", "studio-b", feed="fb", ntfy="nb")
        s.commit()

    processed_users = []
    def fake_fetch(studio_url, weeks_ahead, **kw):
        return []
    def fake_sync(session, user_id, classes, duration_minutes, **kw):
        if session.query(UserSettings).filter_by(user_id=user_id).one().studio_url == "studio-a":
            # Mark then raise, like the real sync_user path.
            s2 = session.query(UserSettings).filter_by(user_id=user_id).one()
            s2.last_sync_status = "needs_reauth"
            session.flush()
            raise NeedsReauth("mock")
        processed_users.append(user_id)
        return SyncDiff()
    with patch("scheduler.fetch_schedule", side_effect=fake_fetch), \
         patch("scheduler.sync_user", side_effect=fake_sync), \
         patch("scheduler.process_diff", MagicMock()):
        sync_all_users(cfg, factory, http=MagicMock())
    # The healthy user still got processed.
    assert len(processed_users) == 1
    with factory() as s:
        a = (s.query(UserSettings)
             .join(User, User.id == UserSettings.user_id)
             .filter(User.google_sub == "ua").one())
        assert a.last_sync_status == "needs_reauth"


def test_start_registers_two_cron_jobs(cfg, engine_and_factory):
    _, factory = engine_and_factory
    sched = start(cfg, factory, http=MagicMock(), autostart=False)
    jobs = {j.id: j for j in sched.get_jobs()}
    assert "daily_sync" in jobs
    assert "daily_digest" in jobs
