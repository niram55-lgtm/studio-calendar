"""Google Calendar sync: diff + batched event CRUD.

Diff functions (compute_diff + SyncDiff) are pure. sync_user and
ensure_calendar talk to the Google Calendar API via `googleapiclient`.
"""
from __future__ import annotations

import datetime as dt
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Callable
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from calendar_ics import loose_uid, strict_uid
from models import ClassSnapshot, GoogleToken, UserSettings
from scraper import ClassItem

logger = logging.getLogger(__name__)
JERUSALEM = ZoneInfo("Asia/Jerusalem")


class NeedsReauth(Exception):
    """Raised when a user's Google refresh fails and they must re-link."""


@dataclass
class SyncDiff:
    added: list[ClassItem] = field(default_factory=list)
    removed: list[ClassSnapshot] = field(default_factory=list)
    changed: list[tuple[ClassSnapshot, ClassItem]] = field(default_factory=list)


def compute_diff(old: list[ClassSnapshot],
                 new: list[ClassItem]) -> SyncDiff:
    """Compute added/removed/changed between stored snapshots and new scrape.

    Primary matching is by loose_uid (date+trainer+class_type) so a time
    shift becomes "changed" (one event moved) rather than removed+added.
    When the same (date, trainer, class_type) appears twice in a day —
    e.g. a double Pilates slot — the loose key collides, so for those
    groups we fall back to matching by strict_uid (which includes time
    and room). Each slot is tracked as its own event; a shift within a
    collision group is reported as removed+added instead of changed.
    """
    new_by_loose: dict[str, list[ClassItem]] = {}
    for c in new:
        new_by_loose.setdefault(loose_uid(c), []).append(c)
    old_by_loose: dict[str, list[ClassSnapshot]] = {}
    for s in old:
        old_by_loose.setdefault(s.loose_uid, []).append(s)

    added: list[ClassItem] = []
    removed: list[ClassSnapshot] = []
    changed: list[tuple[ClassSnapshot, ClassItem]] = []

    for luid in set(new_by_loose) | set(old_by_loose):
        news = new_by_loose.get(luid, [])
        olds = old_by_loose.get(luid, [])
        if len(news) <= 1 and len(olds) <= 1:
            if news and not olds:
                added.extend(news)
            elif olds and not news:
                removed.extend(olds)
            else:
                old_snap = olds[0]
                new_item = news[0]
                if old_snap.strict_uid != strict_uid(new_item):
                    changed.append((old_snap, new_item))
        else:
            new_by_strict = {strict_uid(c): c for c in news}
            old_by_strict = {s.strict_uid: s for s in olds}
            for sid, c in new_by_strict.items():
                if sid not in old_by_strict:
                    added.append(c)
            for sid, s in old_by_strict.items():
                if sid not in new_by_strict:
                    removed.append(s)
    return SyncDiff(added=added, removed=removed, changed=changed)


# ---- Google API sync (used by sync_user) -----------------------------------

def _event_body(c: ClassItem, duration_minutes: int) -> dict:
    start = dt.datetime.combine(c.date, c.time)
    end = start + dt.timedelta(minutes=duration_minutes)
    return {
        "summary": f"{c.class_type} — {c.trainer}",
        "location": f"סטודיו נופר — {c.room}",
        "description": f"{c.class_type} | {c.trainer} | אולם {c.room}",
        "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S"),
                  "timeZone": "Asia/Jerusalem"},
        "end": {"dateTime": end.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "Asia/Jerusalem"},
        "iCalUID": strict_uid(c),
    }


def _retry_execute(request, retries: int = 3):
    """Execute a googleapiclient request with exponential backoff on 429/5xx."""
    for attempt in range(retries):
        try:
            return request.execute()
        except Exception as exc:  # pragma: no cover - network path
            status = getattr(exc, "resp", None)
            code = getattr(status, "status", None) if status is not None else None
            if code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                delay = (2 ** attempt) + random.random()
                time.sleep(delay)
                continue
            raise
    raise RuntimeError("unreachable")


def ensure_calendar(session: Session, user_id: int, display_name: str,
                    *, build_service: Callable | None = None) -> str:
    tok = session.query(GoogleToken).filter_by(user_id=user_id).one()
    if tok.calendar_id:
        return tok.calendar_id

    if build_service is None:  # pragma: no cover - real path uses load_credentials
        raise RuntimeError("build_service must be injected")

    service = build_service(user_id)
    created = _retry_execute(service.calendars().insert(body={
        "summary": display_name,
        "timeZone": "Asia/Jerusalem",
    }))
    tok.calendar_id = created["id"]
    session.flush()
    return tok.calendar_id


def sync_user(
    session: Session,
    user_id: int,
    classes: list[ClassItem],
    duration_minutes: int = 55,
    *,
    build_service: Callable,
    display_name: str = "סטודיו נופר",
) -> SyncDiff:
    """Compute diff vs stored snapshots and apply changes to Google.

    build_service(user_id) returns a googleapiclient service — this lets
    tests inject a fake. Real callers use the module helper
    _default_build_service wired to auth.load_credentials.
    """
    settings = session.query(UserSettings).filter_by(user_id=user_id).one()
    try:
        service = build_service(user_id)
    except Exception as exc:
        from google.auth.exceptions import RefreshError
        if isinstance(exc, RefreshError):
            settings.last_sync_status = "needs_reauth"
            session.flush()
            raise NeedsReauth(str(exc)) from exc
        raise

    calendar_id = ensure_calendar(
        session, user_id, display_name, build_service=lambda _uid: service,
    )

    old = (session.query(ClassSnapshot)
           .filter_by(user_id=user_id).all())
    diff = compute_diff(old=old, new=classes)

    def _status_code(exc: Exception) -> int | None:
        resp = getattr(exc, "resp", None)
        return getattr(resp, "status", None) if resp is not None else None

    def _recreate_calendar() -> str:
        tok_row = (session.query(GoogleToken)
                   .filter_by(user_id=user_id).one())
        tok_row.calendar_id = None
        session.flush()
        return ensure_calendar(
            session, user_id, display_name,
            build_service=lambda _uid: service,
        )

    try:
        # DELETE removed. Only drop the snapshot if the event is confirmed
        # gone from Google; a transient API failure must leave the
        # snapshot so the next sync retries instead of orphaning the event.
        for snap in diff.removed:
            if snap.google_event_id:
                try:
                    _retry_execute(service.events().delete(
                        calendarId=calendar_id, eventId=snap.google_event_id))
                    session.delete(snap)
                except Exception as exc:  # pragma: no cover
                    code = _status_code(exc)
                    if code in (404, 410):
                        # Already gone — safe to drop the snapshot.
                        session.delete(snap)
                    else:
                        logger.warning(
                            "delete failed, keeping snapshot %s for retry: %s",
                            snap.id, exc)
            else:
                session.delete(snap)

        # UPDATE changed
        for old_snap, new_item in diff.changed:
            body = _event_body(new_item, duration_minutes)
            if old_snap.google_event_id:
                try:
                    _retry_execute(service.events().update(
                        calendarId=calendar_id,
                        eventId=old_snap.google_event_id,
                        body=body,
                    ))
                except Exception as exc:  # pragma: no cover
                    logger.warning("update failed: %s", exc)
            old_snap.strict_uid = strict_uid(new_item)
            old_snap.class_time = new_item.time.strftime("%H:%M")
            old_snap.room = new_item.room

        # INSERT added. If the calendar was deleted in Google UI, insert
        # 404s — recreate the calendar once and retry.
        for new_item in diff.added:
            body = _event_body(new_item, duration_minutes)
            try:
                created = _retry_execute(service.events().insert(
                    calendarId=calendar_id, body=body,
                ))
            except Exception as exc:
                if _status_code(exc) == 404:
                    calendar_id = _recreate_calendar()
                    created = _retry_execute(service.events().insert(
                        calendarId=calendar_id, body=body,
                    ))
                else:
                    raise
            session.add(ClassSnapshot(
                user_id=user_id,
                loose_uid=loose_uid(new_item),
                strict_uid=strict_uid(new_item),
                class_date=new_item.date,
                class_time=new_item.time.strftime("%H:%M"),
                trainer=new_item.trainer,
                class_type=new_item.class_type,
                room=new_item.room,
                google_event_id=created.get("id"),
            ))

        settings.last_sync_at = dt.datetime.now(dt.timezone.utc)
        settings.last_sync_status = "ok"
        session.flush()
    except Exception as exc:
        from google.auth.exceptions import RefreshError
        if isinstance(exc, RefreshError):
            settings.last_sync_status = "needs_reauth"
            session.flush()
            raise NeedsReauth(str(exc)) from exc
        settings.last_sync_status = f"error:{exc}"
        session.flush()
        raise

    return diff
