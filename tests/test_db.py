"""Tests for DB engine and models."""
from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from db import get_engine, init_db, SessionLocal
from models import (
    Base, User, GoogleToken, UserSettings, Trainer, ClassSnapshot,
    PendingDigest, NotificationsLog, ScrapeCache,
)


@pytest.fixture
def engine():
    eng = get_engine("sqlite:///:memory:")
    init_db(eng)
    SessionLocal.configure(bind=eng)
    return eng


def test_init_db_creates_all_tables(engine):
    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    assert tables >= {
        "users", "google_tokens", "user_settings", "trainers",
        "class_snapshots", "pending_digest", "notifications_log",
        "scrape_cache",
    }


def _mk_user_with_settings(session, sub="g1"):
    u = User(google_sub=sub, email=f"{sub}@x", display_name=sub)
    session.add(u)
    session.flush()
    s = UserSettings(user_id=u.id, feed_token=f"feed{sub}", ntfy_topic=f"ntfy{sub}")
    session.add(s)
    session.flush()
    return u


def test_user_settings_cascade_delete(engine):
    with SessionLocal() as session:
        u = _mk_user_with_settings(session)
        session.add(Trainer(user_id=u.id, trainer_name="שירן"))
        session.add(GoogleToken(
            user_id=u.id,
            encrypted_access=b"a",
            encrypted_refresh=b"b",
            expiry=sa.func.now(),
        ))
        session.commit()
        uid = u.id
        session.delete(u)
        session.commit()
        assert session.query(UserSettings).filter_by(user_id=uid).count() == 0
        assert session.query(Trainer).filter_by(user_id=uid).count() == 0
        assert session.query(GoogleToken).filter_by(user_id=uid).count() == 0


def test_scrape_cache_pk_is_studio_url_plus_week_start(engine):
    import datetime as dt
    with SessionLocal() as session:
        session.add(ScrapeCache(studio_url="s1", week_start=dt.date(2026, 2, 1),
                                html="<a/>"))
        session.commit()
        session.add(ScrapeCache(studio_url="s1", week_start=dt.date(2026, 2, 1),
                                html="<b/>"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_trainers_unique_per_user(engine):
    with SessionLocal() as session:
        u = _mk_user_with_settings(session)
        session.add(Trainer(user_id=u.id, trainer_name="שירן"))
        session.commit()
        session.add(Trainer(user_id=u.id, trainer_name="שירן"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
