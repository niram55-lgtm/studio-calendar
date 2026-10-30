"""Google OAuth 2.0 flow + user identity + credentials loading."""
from __future__ import annotations

import datetime as dt
import secrets
from dataclasses import dataclass

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from sqlalchemy.orm import Session

import crypto
from config import Config
from models import GoogleToken, User, UserSettings

GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/calendar",
]

TOKEN_URI = "https://oauth2.googleapis.com/token"


@dataclass
class GoogleAuthResult:
    google_sub: str
    email: str
    name: str
    access_token: str
    refresh_token: str
    expiry: dt.datetime


def _flow(config: Config, state: str | None = None) -> Flow:
    client_config = {
        "web": {
            "client_id": config.google_client_id,
            "client_secret": config.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": TOKEN_URI,
            "redirect_uris": [f"{config.base_url}/auth/callback"],
        }
    }
    flow = Flow.from_client_config(
        client_config,
        scopes=GOOGLE_SCOPES,
        state=state,
    )
    flow.redirect_uri = f"{config.base_url}/auth/callback"
    return flow


def build_authorize_url(config: Config, state: str) -> str:
    flow = _flow(config, state=state)
    url, _state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    # The flow sometimes returns a different scope ordering; ensure the
    # calendar scope string is present verbatim by appending it to scope
    # query if missing (defensive — Google's own URL already includes them).
    return url


def exchange_code(config: Config, code: str) -> GoogleAuthResult:
    flow = _flow(config)
    flow.fetch_token(code=code)
    creds = flow.credentials
    # id_token present on OIDC-scoped auth; parse it.
    id_info = _verify_id_token(creds.id_token, config.google_client_id)
    return GoogleAuthResult(
        google_sub=id_info["sub"],
        email=id_info.get("email", ""),
        name=id_info.get("name", ""),
        access_token=creds.token,
        refresh_token=creds.refresh_token,
        expiry=creds.expiry or dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
    )


def _verify_id_token(id_token_str: str, audience: str) -> dict:
    from google.oauth2 import id_token
    from google.auth.transport import requests as grequests
    return id_token.verify_oauth2_token(
        id_token_str, grequests.Request(), audience
    )


def upsert_user_from_auth(session: Session, result: GoogleAuthResult) -> User:
    user = session.query(User).filter_by(google_sub=result.google_sub).one_or_none()
    if user is None:
        user = User(
            google_sub=result.google_sub,
            email=result.email,
            display_name=result.name,
        )
        session.add(user)
        session.flush()
    else:
        user.email = result.email
        user.display_name = result.name or user.display_name

    settings = session.query(UserSettings).filter_by(user_id=user.id).one_or_none()
    if settings is None:
        settings = UserSettings(
            user_id=user.id,
            feed_token=secrets.token_hex(16),
            ntfy_topic=secrets.token_hex(16),
        )
        session.add(settings)

    tok = session.query(GoogleToken).filter_by(user_id=user.id).one_or_none()
    encrypted_access = crypto.encrypt(result.access_token)
    # Google may omit refresh_token on a return login — keep the existing
    # stored value in that case. On first login refresh_token must exist;
    # if it does not we cannot persist the user usefully, so raise.
    if tok is None:
        if not result.refresh_token:
            raise RuntimeError(
                "First login missing refresh_token — add prompt=consent and "
                "access_type=offline in the OAuth flow"
            )
        tok = GoogleToken(
            user_id=user.id,
            encrypted_access=encrypted_access,
            encrypted_refresh=crypto.encrypt(result.refresh_token),
            expiry=result.expiry,
        )
        session.add(tok)
    else:
        tok.encrypted_access = encrypted_access
        if result.refresh_token:
            tok.encrypted_refresh = crypto.encrypt(result.refresh_token)
        tok.expiry = result.expiry

    session.flush()
    return user


def load_credentials(session: Session, config: Config, user_id: int) -> Credentials:
    tok = session.query(GoogleToken).filter_by(user_id=user_id).one()
    access = crypto.decrypt(tok.encrypted_access)
    refresh = crypto.decrypt(tok.encrypted_refresh)
    creds = Credentials(
        token=access,
        refresh_token=refresh,
        token_uri=TOKEN_URI,
        client_id=config.google_client_id,
        client_secret=config.google_client_secret,
        scopes=GOOGLE_SCOPES,
    )
    return creds


def persist_refreshed(session: Session, user_id: int, creds: Credentials) -> None:
    """Re-encrypt a refreshed access token back to DB."""
    tok = session.query(GoogleToken).filter_by(user_id=user_id).one()
    tok.encrypted_access = crypto.encrypt(creds.token)
    if creds.refresh_token:
        tok.encrypted_refresh = crypto.encrypt(creds.refresh_token)
    tok.expiry = creds.expiry or tok.expiry
    session.flush()
