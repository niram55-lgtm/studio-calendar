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


def test_sync_all_users_skips_when_unfiltered_scrape_is_empty_and_snapshots_exist(cfg, engine_and_factory):
    """Scraper returning [] while the DB has snapshots is a near-certain
    scrape failure (BoostApp down, HTML drift, holiday page). The scheduler
    must skip sync for that user instead of wiping their calendar."""
    from models import Trainer, ClassSnapshot
    import datetime as _dt
    _, factory = engine_and_factory
    with factory() as s:
        u = _mk_user(s, "u1", "studio1", feed="f1", ntfy="n1")
        s.add(Trainer(user_id=u.id, trainer_name="pick-me", enabled=True))
        s.add(ClassSnapshot(
            user_id=u.id, loose_uid="loose", strict_uid="strict",
            class_date=_dt.date(2026, 2, 2), class_time="18:00",
            trainer="pick-me", class_type="X", room="R",
            google_event_id="event-stay",
        ))
        s.commit()

    sync_calls = []

    def fake_fetch(studio_url, weeks_ahead, **kw):
        return []  # scrape says nothing — do NOT trust it.

    def fake_sync(session, user_id, classes, duration_minutes, **kw):
        sync_calls.append(user_id)
        return SyncDiff()

    with patch("scheduler.fetch_schedule", side_effect=fake_fetch), \
         patch("scheduler.sync_user", side_effect=fake_sync), \
         patch("scheduler.process_diff", MagicMock()):
        sync_all_users(cfg, factory, http=MagicMock())
    assert sync_calls == []  # must not have called sync_user at all
    with factory() as s:
        settings = s.query(UserSettings).filter_by(user_id=1).one()
        assert settings.last_sync_status == "skipped:empty_scrape_with_snapshots"


def test_sync_all_users_passes_only_enabled_trainers_to_sync(cfg, engine_and_factory):
    """Scheduler must filter classes by the user's enabled trainers before
    handing them to sync_user — otherwise cron pushes every trainer's class
    while manual /api/sync (which filters) stays correct."""
    from scraper import ClassItem
    import datetime as _dt
    from models import Trainer
    _, factory = engine_and_factory
    with factory() as s:
        u = _mk_user(s, "u1", "studio1", feed="f1", ntfy="n1")
        s.add(Trainer(user_id=u.id, trainer_name="pick-me", enabled=True))
        s.add(Trainer(user_id=u.id, trainer_name="skip-me", enabled=False))
        s.commit()

    passed_trainers: list[str] = []

    def fake_fetch(studio_url, weeks_ahead, **kw):
        return [
            ClassItem(date=_dt.date(2026, 2, 2), time=_dt.time(18, 0),
                      trainer="pick-me", class_type="X", room="R",
                      source="live"),
            ClassItem(date=_dt.date(2026, 2, 2), time=_dt.time(19, 0),
                      trainer="skip-me", class_type="Y", room="R",
                      source="live"),
        ]

    def fake_sync(session, user_id, classes, duration_minutes, **kw):
        passed_trainers.extend(c.trainer for c in classes)
        return SyncDiff()

    with patch("scheduler.fetch_schedule", side_effect=fake_fetch), \
         patch("scheduler.sync_user", side_effect=fake_sync), \
         patch("scheduler.process_diff", MagicMock()):
        sync_all_users(cfg, factory, http=MagicMock())
    assert passed_trainers == ["pick-me"]
