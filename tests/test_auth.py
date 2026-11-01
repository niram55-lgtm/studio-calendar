"""Tests for auth.py — Google OAuth flow + user upsert."""
from __future__ import annotations

import datetime as dt
import re
from urllib.parse import urlparse, parse_qs

import pytest

import auth
import crypto
from auth import GoogleAuthResult, build_authorize_url, upsert_user_from_auth
from config import load_config
from db import get_engine, init_db, SessionLocal
from models import User, UserSettings, GoogleToken


@pytest.fixture
def cfg():
    return load_config("config.example.yaml")


@pytest.fixture
def session():
    eng = get_engine("sqlite:///:memory:")
    init_db(eng)
    SessionLocal.configure(bind=eng)
    crypto._FERNET = None  # ensure fresh
    with SessionLocal() as s:
        yield s


def test_build_authorize_url_contains_scopes_and_redirect(cfg):
    url, verifier = build_authorize_url(cfg, state="xyz")
    assert verifier and len(verifier) >= 32
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert qs["state"] == ["xyz"]
    assert qs["redirect_uri"] == [f"{cfg.base_url}/auth/callback"]
    scope = qs["scope"][0]
    for s in ["openid", "email", "profile",
              "https://www.googleapis.com/auth/calendar"]:
        assert s in scope


def _result(sub="sub-1", email="x@y") -> GoogleAuthResult:
    return GoogleAuthResult(
        google_sub=sub,
        email=email,
        name="Test User",
        access_token="access-xyz",
        refresh_token="refresh-xyz",
        expiry=dt.datetime(2026, 10, 30, 10, 0, 0),
    )


def test_upsert_user_creates_settings_with_tokens(session):
    user = upsert_user_from_auth(session, _result())
    session.commit()
    assert user.id is not None
    settings = session.query(UserSettings).filter_by(user_id=user.id).one()
    assert re.fullmatch(r"[0-9a-f]{32}", settings.feed_token)
    assert re.fullmatch(r"[0-9a-f]{32}", settings.ntfy_topic)
    tok = session.query(GoogleToken).filter_by(user_id=user.id).one()
    assert crypto.decrypt(tok.encrypted_access) == "access-xyz"
    assert crypto.decrypt(tok.encrypted_refresh) == "refresh-xyz"


def test_upsert_user_second_login_reuses_settings(session):
    u1 = upsert_user_from_auth(session, _result())
    session.commit()
    settings_before = session.query(UserSettings).filter_by(user_id=u1.id).one()
    old_feed = settings_before.feed_token

    r2 = _result()
    r2.access_token = "new-access"
    r2.refresh_token = "new-refresh"
    u2 = upsert_user_from_auth(session, r2)
    session.commit()
    assert u2.id == u1.id
    assert session.query(UserSettings).count() == 1
    settings_after = session.query(UserSettings).filter_by(user_id=u2.id).one()
    assert settings_after.feed_token == old_feed  # unchanged
    tok = session.query(GoogleToken).filter_by(user_id=u2.id).one()
    assert crypto.decrypt(tok.encrypted_access) == "new-access"
    assert crypto.decrypt(tok.encrypted_refresh) == "new-refresh"


def test_upsert_user_second_login_without_refresh_token_keeps_existing(session):
    """Google may omit refresh_token on a return login (no prompt=consent,
    or because the user already granted). Keep the stored refresh token."""
    u1 = upsert_user_from_auth(session, _result())
    session.commit()
    tok_before = session.query(GoogleToken).filter_by(user_id=u1.id).one()
    stored_refresh_before = crypto.decrypt(tok_before.encrypted_refresh)

    r2 = _result()
    r2.access_token = "new-access"
    r2.refresh_token = None  # Google omitted it on this round-trip
    u2 = upsert_user_from_auth(session, r2)
    session.commit()
    tok_after = session.query(GoogleToken).filter_by(user_id=u2.id).one()
    assert crypto.decrypt(tok_after.encrypted_access) == "new-access"
    assert crypto.decrypt(tok_after.encrypted_refresh) == stored_refresh_before


def test_load_credentials_decrypts_and_returns_valid_object(cfg, session):
    user = upsert_user_from_auth(session, _result())
    session.commit()
    creds = auth.load_credentials(session, cfg, user.id)
    assert creds.refresh_token == "refresh-xyz"
    assert creds.token == "access-xyz"
    assert creds.client_id == cfg.google_client_id
    assert creds.client_secret == cfg.google_client_secret
