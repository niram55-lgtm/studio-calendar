"""APScheduler jobs: daily sync + daily digest."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Callable

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from googleapiclient.discovery import build as build_google_service

from auth import load_credentials
from calendar_google import NeedsReauth, sync_user
from config import Config
from models import ClassSnapshot, Trainer, User, UserSettings
from notifications import process_diff, send_digest
from scraper import fetch_schedule

logger = logging.getLogger(__name__)


def _make_build_service(config: Config, session) -> Callable[[int], object]:
    def _build(user_id: int):
        creds = load_credentials(session, config, user_id)
        return build_google_service("calendar", "v3", credentials=creds,
                                    cache_discovery=False)
    return _build


def sync_all_users(config: Config, session_factory, http: requests.Session) -> None:
    with session_factory() as session:
        users = (session.query(User)
                 .join(UserSettings, UserSettings.user_id == User.id)
                 .filter(UserSettings.studio_url.isnot(None))
                 .all())
        for u in users:
            try:
                settings = (session.query(UserSettings)
                            .filter_by(user_id=u.id).one())
                raw_classes = fetch_schedule(
                    settings.studio_url,
                    weeks_ahead=settings.weeks_ahead or 1,
                    session=session,
                    http=http,
                    cache_ttl_hours=config.scheduler.cache_ttl_hours,
                    base_url=config.boostapp.base_url,
                    user_agent=config.boostapp.user_agent,
                    request_timeout=config.boostapp.request_timeout,
                )
                # Safety: empty UNFILTERED scrape + existing snapshots =
                # near-certain scrape failure. Refuse to wipe the user's
                # calendar; a real cancellation would still leave other
                # classes in the page.
                snapshot_count = (session.query(ClassSnapshot)
                                  .filter_by(user_id=u.id).count())
                if not raw_classes and snapshot_count > 0:
                    settings.last_sync_status = "skipped:empty_scrape_with_snapshots"
                    session.commit()
                    logger.warning(
                        "user %s: empty scrape with %d snapshots — skipping",
                        u.id, snapshot_count,
                    )
                    continue
                enabled = {t.trainer_name for t in session.query(Trainer)
                           .filter_by(user_id=u.id, enabled=True).all()}
                classes = [c for c in raw_classes if c.trainer in enabled]
                diff = sync_user(
                    session, u.id, classes,
                    duration_minutes=settings.duration_minutes or 55,
                    build_service=_make_build_service(config, session),
                )
                process_diff(
                    session, u.id, diff,
                    now=dt.datetime.now(dt.timezone.utc),
                    http=http,
                    ntfy_server=config.ntfy.server,
                )
                session.commit()
            except NeedsReauth as exc:
                logger.warning("user %s needs reauth: %s", u.id, exc)
                session.commit()
                continue
            except Exception as exc:
                logger.exception("sync failed for user %s: %s", u.id, exc)
                session.rollback()
                continue


def send_all_digests(config: Config, session_factory, http: requests.Session) -> None:
    with session_factory() as session:
        users = (session.query(User)
                 .join(UserSettings, UserSettings.user_id == User.id)
                 .all())
        now = dt.datetime.now(dt.timezone.utc)
        for u in users:
            try:
                send_digest(session, u.id, now=now, http=http,
                            ntfy_server=config.ntfy.server)
                session.commit()
            except Exception as exc:  # pragma: no cover
                logger.exception("digest failed for user %s: %s", u.id, exc)
                session.rollback()


def _parse_hhmm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def start(
    config: Config,
    session_factory,
    http: requests.Session,
    *,
    autostart: bool = True,
) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="Asia/Jerusalem")
    scheduler.add_job(
        sync_all_users, "cron",
        hour=config.scheduler.daily_sync_hour,
        minute=config.scheduler.daily_sync_minute,
        id="daily_sync",
        args=[config, session_factory, http],
    )
    dh, dm = _parse_hhmm(config.defaults.digest_time)
    scheduler.add_job(
        send_all_digests, "cron",
        hour=dh, minute=dm,
        id="daily_digest",
        args=[config, session_factory, http],
    )
    if autostart:
        scheduler.start()
    return scheduler
