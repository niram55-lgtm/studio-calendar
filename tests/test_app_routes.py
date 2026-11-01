"""Tests for Flask routes."""
from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest

from app import create_app
from config import load_config
from db import get_engine, init_db, SessionLocal
from models import (
    User, UserSettings, Trainer, ClassSnapshot, ScrapeCache,
)
from calendar_ics import loose_uid, strict_uid
from scraper import ClassItem


@pytest.fixture
def app():
    eng = get_engine("sqlite:///:memory:")
    init_db(eng)
    SessionLocal.configure(bind=eng)
    cfg = load_config("config.example.yaml")
    flask_app = create_app(cfg, session_factory=SessionLocal)
    flask_app.config["TESTING"] = True
    yield flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def test_root_redirects_to_login_when_not_authenticated(client):
    r = client.get("/")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.data == b"ok"


def _seed_user_with_cache(session, html: str, feed="feed-abc", enabled_trainer="שירן אמויאל"):
    u = User(google_sub="g1", email="x@y", display_name="x")
    session.add(u)
    session.flush()
    session.add(UserSettings(
        user_id=u.id, feed_token=feed, ntfy_topic="n1",
        studio_url="636a5a359ccfa",
    ))
    session.add(Trainer(user_id=u.id, trainer_name=enabled_trainer, enabled=True))
    session.add(Trainer(user_id=u.id, trainer_name="אחרת", enabled=False))
    session.commit()


def test_feed_route_returns_ics_for_enabled_trainers(client, app):
    from pathlib import Path
    html = (Path(__file__).parent / "fixtures" / "boost_week_2026-02-02.html").read_text(encoding="utf-8")
    with SessionLocal() as session:
        _seed_user_with_cache(session, html, feed="feed-abc",
                              enabled_trainer="שירן אמויאל")
        # Seed cache so the feed route doesn't need live network.
        session.add(ScrapeCache(
            studio_url="636a5a359ccfa",
            # week_start must be within the feed cutoff (today - 7d).
            week_start=dt.date.today(),
            html=html,
            fetched_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        ))
        session.commit()

    r = client.get("/feed/feed-abc.ics")
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/calendar")
    body = r.data.decode("utf-8")
    assert "שירן אמויאל" in body
    assert "אחרת" not in body


def test_feed_route_404_on_unknown_token(client):
    r = client.get("/feed/does-not-exist.ics")
    assert r.status_code == 404


def test_api_settings_rejects_bad_urgent_window(client):
    with SessionLocal() as session:
        u = User(google_sub="g2", email="y@y", display_name="y")
        session.add(u)
        session.flush()
        session.add(UserSettings(user_id=u.id, feed_token="f2", ntfy_topic="n2"))
        session.commit()
        uid = u.id
    with client.session_transaction() as s:
        s["user_id"] = uid
    r = client.post("/api/settings", json={"urgent_window_hours": 999})
    assert r.status_code == 400


def test_api_load_persists_studio_url(client):
    with SessionLocal() as session:
        u = User(google_sub="g3", email="y@y", display_name="y")
        session.add(u)
        session.flush()
        session.add(UserSettings(user_id=u.id, feed_token="f3", ntfy_topic="n3"))
        session.commit()
        uid = u.id
    with client.session_transaction() as s:
        s["user_id"] = uid
    with patch("app.fetch_schedule", return_value=[]):
        r = client.post("/api/load", json={
            "link": "https://app.boostapp.co.il/Rest.php?datepicker=02/02&StudioUrl=636a5a359ccfa"
        })
    assert r.status_code == 200
    with SessionLocal() as s:
        settings = s.query(UserSettings).filter_by(user_id=uid).one()
        assert settings.studio_url == "636a5a359ccfa"


def test_api_trainers_post_toggles_enabled_flag(client):
    with SessionLocal() as session:
        u = User(google_sub="g4", email="y@y", display_name="y")
        session.add(u)
        session.flush()
        session.add(UserSettings(user_id=u.id, feed_token="f4", ntfy_topic="n4"))
        session.add(Trainer(user_id=u.id, trainer_name="A", enabled=False))
        session.add(Trainer(user_id=u.id, trainer_name="B", enabled=False))
        session.commit()
        uid = u.id
    with client.session_transaction() as s:
        s["user_id"] = uid
    r = client.post("/api/trainers", json={"enabled_names": ["A"]})
    assert r.status_code == 200
    with SessionLocal() as s:
        rows = {t.trainer_name: t.enabled
                for t in s.query(Trainer).filter_by(user_id=uid).all()}
        assert rows == {"A": True, "B": False}


def test_csrf_rejects_non_json_post_to_api(client):
    """CSRF: a cross-origin form POST (text/plain) must be blocked."""
    with SessionLocal() as session:
        u = User(google_sub="g-csrf", email="z@z", display_name="z")
        session.add(u)
        session.flush()
        session.add(UserSettings(user_id=u.id, feed_token="fc", ntfy_topic="nc"))
        session.commit()
        uid = u.id
    with client.session_transaction() as s:
        s["user_id"] = uid
    r = client.post("/api/trainers",
                    data="enabled_names=[]",
                    content_type="text/plain")
    assert r.status_code == 415


def test_csrf_rejects_cross_origin_json_post(client):
    with SessionLocal() as session:
        u = User(google_sub="g-xorg", email="z2@z", display_name="z2")
        session.add(u)
        session.flush()
        session.add(UserSettings(user_id=u.id, feed_token="fx", ntfy_topic="nx"))
        session.commit()
        uid = u.id
    with client.session_transaction() as s:
        s["user_id"] = uid
    r = client.post("/api/trainers",
                    json={"enabled_names": []},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
