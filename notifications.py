"""ntfy.sh push + digest for sync diffs."""
from __future__ import annotations

import datetime as dt
import json
import logging
from email.header import Header
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from calendar_google import SyncDiff
from calendar_ics import loose_uid
from models import NotificationsLog, PendingDigest, UserSettings
from scraper import ClassItem

logger = logging.getLogger(__name__)
JERUSALEM = ZoneInfo("Asia/Jerusalem")

HEBREW_DAYS = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]


def _encode_title(title: str) -> str:
    """ntfy requires non-ASCII titles RFC-2047 encoded."""
    try:
        title.encode("ascii")
        return title
    except UnicodeEncodeError:
        return Header(title, "utf-8").encode()


def push(
    http,
    ntfy_server: str,
    topic: str,
    title: str,
    body: str,
    priority: int = 4,
    tags: list[str] | None = None,
) -> None:
    headers = {
        "Title": _encode_title(title),
        "Priority": str(priority),
    }
    if tags:
        headers["Tags"] = ",".join(tags)
    url = f"{ntfy_server.rstrip('/')}/{topic}"
    try:
        resp = http.post(url, data=body.encode("utf-8"), headers=headers,
                         timeout=15)
        resp.raise_for_status()
    except Exception as exc:  # pragma: no cover
        logger.warning("ntfy push failed: %s", exc)


def _format_date_he(d: dt.date, t: dt.time) -> str:
    weekday_name = HEBREW_DAYS[d.weekday()]
    return f"יום {weekday_name} {d.strftime('%d/%m')} {t.strftime('%H:%M')}"


def _format_cancellation_title(c_date, c_time, class_type, trainer) -> str:
    return f"⚠️ בוטל: {class_type} — {trainer}"


def _format_cancellation_body(c_date, c_time, room) -> str:
    return f"{_format_date_he(c_date, c_time)} · {room}"


def _format_change_title(class_type, trainer) -> str:
    return f"🔄 שינוי: {class_type} — {trainer}"


def _format_change_body(old_date, old_time, new_date, new_time, room) -> str:
    if old_time == new_time:
        return (f"{_format_date_he(old_date, old_time)} · {room}")
    return (f"{_format_date_he(old_date, old_time)} → "
            f"{new_time.strftime('%H:%M')} · {room}")


def _format_added_title(class_type, trainer) -> str:
    return f"➕ שיעור חדש: {class_type} — {trainer}"


def _format_added_body(c_date, c_time, room) -> str:
    return f"{_format_date_he(c_date, c_time)} · {room}"


def _class_start_utc(d: dt.date, t_hhmm: str | dt.time) -> dt.datetime:
    if isinstance(t_hhmm, str):
        hh, mm = t_hhmm.split(":")
        tm = dt.time(int(hh), int(mm))
    else:
        tm = t_hhmm
    local = dt.datetime.combine(d, tm, tzinfo=JERUSALEM)
    return local.astimezone(dt.timezone.utc)


def _log_or_skip(session: Session, user_id: int, luid: str,
                 change_type: str) -> bool:
    """Insert into notifications_log; return False if already present."""
    exists = (session.query(NotificationsLog)
              .filter_by(user_id=user_id, loose_uid=luid,
                         change_type=change_type).count())
    if exists:
        return False
    session.add(NotificationsLog(
        user_id=user_id, loose_uid=luid, change_type=change_type,
    ))
    try:
        session.flush()
        return True
    except IntegrityError:
        session.rollback()
        return False


def process_diff(
    session: Session,
    user_id: int,
    diff: SyncDiff,
    *,
    now: dt.datetime,
    http,
    ntfy_server: str,
) -> None:
    settings = session.query(UserSettings).filter_by(user_id=user_id).one()
    urgent = dt.timedelta(hours=settings.urgent_window_hours)

    def _emit(title: str, body: str, luid: str, change_type: str,
              start_utc: dt.datetime) -> None:
        if not _log_or_skip(session, user_id, luid, change_type):
            return
        delta = start_utc - now
        if delta <= urgent and delta >= dt.timedelta(hours=-1):
            push(http, ntfy_server, settings.ntfy_topic, title, body,
                 priority=5 if change_type == "removed" else 4,
                 tags=["warning"] if change_type == "removed" else None)
        else:
            session.add(PendingDigest(
                user_id=user_id, change_type=change_type,
                payload_json=json.dumps({"title": title, "body": body},
                                        ensure_ascii=False),
            ))
            session.flush()

    if settings.notify_on_cancellation:
        for snap in diff.removed:
            tm = dt.time(*map(int, snap.class_time.split(":")))
            start_utc = _class_start_utc(snap.class_date, tm)
            _emit(
                _format_cancellation_title(snap.class_date, tm,
                                           snap.class_type, snap.trainer),
                _format_cancellation_body(snap.class_date, tm, snap.room),
                snap.loose_uid, "removed", start_utc,
            )

    if settings.notify_on_change:
        for old_snap, new_item in diff.changed:
            start_utc = _class_start_utc(new_item.date, new_item.time)
            old_tm = dt.time(*map(int, old_snap.class_time.split(":")))
            _emit(
                _format_change_title(new_item.class_type, new_item.trainer),
                _format_change_body(old_snap.class_date, old_tm,
                                    new_item.date, new_item.time,
                                    new_item.room),
                loose_uid(new_item), "changed", start_utc,
            )

    if settings.notify_on_new_class:
        for new_item in diff.added:
            start_utc = _class_start_utc(new_item.date, new_item.time)
            _emit(
                _format_added_title(new_item.class_type, new_item.trainer),
                _format_added_body(new_item.date, new_item.time,
                                   new_item.room),
                loose_uid(new_item), "added", start_utc,
            )


def send_digest(
    session: Session,
    user_id: int,
    *,
    now: dt.datetime,
    http,
    ntfy_server: str,
) -> None:
    settings = session.query(UserSettings).filter_by(user_id=user_id).one()
    pending = (session.query(PendingDigest)
               .filter_by(user_id=user_id).all())
    if not pending:
        return
    lines = []
    for row in pending:
        try:
            payload = json.loads(row.payload_json)
            lines.append(f"• {payload.get('title','')}: {payload.get('body','')}")
        except Exception:
            lines.append(row.payload_json)
    title = f"📋 עדכוני לוח ({len(pending)} שינויים)"
    body = "\n".join(lines)
    push(http, ntfy_server, settings.ntfy_topic, title, body, priority=3)
    for row in pending:
        session.delete(row)
    session.flush()
