"""Flask web app: routes, session, OAuth entry/exit, ICS feed."""
from __future__ import annotations

import datetime as dt
import logging
import re
import secrets

import requests
from flask import (
    Flask, Response, abort, g, jsonify, redirect, render_template,
    request, session, url_for,
)

import auth
from calendar_google import NeedsReauth, sync_user
from calendar_ics import build_ics
from config import Config, load_config
from db import SessionLocal, get_engine, init_db
from models import (
    ClassSnapshot, GoogleToken, ScrapeCache, Trainer, User, UserSettings,
)
from notifications import process_diff
from scraper import ClassItem, extract_studio_id, fetch_schedule, parse_schedule_html

logger = logging.getLogger(__name__)

PUBLIC_PATHS = {"/login", "/auth/google", "/auth/callback", "/healthz"}
HHMM_RE = re.compile(r"^\d{2}:\d{2}$")


def create_app(config: Config, *, session_factory=None) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = config.secret_key
    app.config["STUDIO_CONFIG"] = config
    app.config["SESSION_FACTORY"] = session_factory or SessionLocal

    @app.before_request
    def _attach_session():
        g.db_session = app.config["SESSION_FACTORY"]()

    @app.teardown_request
    def _close_session(exc):
        s = getattr(g, "db_session", None)
        if s is None:
            return
        if exc is not None:
            s.rollback()
        else:
            try:
                s.commit()
            except Exception:  # pragma: no cover
                s.rollback()
                raise
        s.close()

    @app.before_request
    def _auth_gate():
        path = request.path
        if (path.startswith("/static/") or path.startswith("/feed/")
                or path in PUBLIC_PATHS):
            return None
        if "user_id" not in session:
            return redirect(url_for("login"))

    @app.route("/healthz")
    def healthz():
        return Response("ok", mimetype="text/plain")

    @app.route("/login")
    def login():
        return render_template("login.html")

    @app.route("/auth/google")
    def auth_google():
        state = secrets.token_urlsafe(32)
        session["oauth_state"] = state
        url = auth.build_authorize_url(config, state=state)
        return redirect(url)

    @app.route("/auth/callback")
    def auth_callback():
        code = request.args.get("code")
        state = request.args.get("state")
        if not code or state != session.get("oauth_state"):
            return "Invalid state", 400
        result = auth.exchange_code(config, code)
        user = auth.upsert_user_from_auth(g.db_session, result)
        g.db_session.commit()
        session["user_id"] = user.id
        session.pop("oauth_state", None)
        return redirect(url_for("index"))

    @app.route("/auth/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    def index():
        u = g.db_session.get(User, session["user_id"])
        settings = (g.db_session.query(UserSettings)
                    .filter_by(user_id=u.id).one())
        trainers = (g.db_session.query(Trainer)
                    .filter_by(user_id=u.id).all())
        return render_template(
            "index.html",
            user=u,
            settings=settings,
            trainers=trainers,
            base_url=config.base_url,
        )

    def _current_settings() -> UserSettings:
        return (g.db_session.query(UserSettings)
                .filter_by(user_id=session["user_id"]).one())

    @app.route("/api/load", methods=["POST"])
    def api_load():
        data = request.get_json(silent=True) or {}
        link = data.get("link", "").strip()
        try:
            studio_id = extract_studio_id(link)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        settings = _current_settings()
        settings.studio_url = studio_id

        http = requests.Session()
        classes = fetch_schedule(
            studio_id,
            weeks_ahead=settings.weeks_ahead or 1,
            session=g.db_session,
            http=http,
            cache_ttl_hours=config.scheduler.cache_ttl_hours,
            base_url=config.boostapp.base_url,
            user_agent=config.boostapp.user_agent,
            request_timeout=config.boostapp.request_timeout,
        )
        trainer_counts: dict[str, int] = {}
        for c in classes:
            trainer_counts[c.trainer] = trainer_counts.get(c.trainer, 0) + 1

        # Upsert trainers; keep existing enabled flags.
        existing = {t.trainer_name: t for t in g.db_session.query(Trainer)
                    .filter_by(user_id=settings.user_id).all()}
        for name in trainer_counts:
            if name not in existing:
                g.db_session.add(Trainer(user_id=settings.user_id,
                                         trainer_name=name, enabled=False))
        g.db_session.commit()

        return jsonify({
            "studio_url": studio_id,
            "weeks": settings.weeks_ahead or 1,
            "trainers": [
                {"name": name, "class_count": count,
                 "enabled": existing.get(name).enabled if name in existing else False}
                for name, count in sorted(trainer_counts.items(),
                                           key=lambda x: -x[1])
            ],
        })

    @app.route("/api/trainers", methods=["GET"])
    def api_trainers_get():
        rows = (g.db_session.query(Trainer)
                .filter_by(user_id=session["user_id"]).all())
        return jsonify([
            {"name": t.trainer_name, "enabled": t.enabled} for t in rows
        ])

    @app.route("/api/trainers", methods=["POST"])
    def api_trainers_post():
        data = request.get_json(silent=True) or {}
        enabled_names = set(data.get("enabled_names", []))
        rows = (g.db_session.query(Trainer)
                .filter_by(user_id=session["user_id"]).all())
        for t in rows:
            t.enabled = t.trainer_name in enabled_names
        g.db_session.flush()
        return jsonify({"ok": True})

    @app.route("/api/settings", methods=["GET", "POST"])
    def api_settings():
        s = _current_settings()
        if request.method == "GET":
            return jsonify({
                "urgent_window_hours": s.urgent_window_hours,
                "duration_minutes": s.duration_minutes,
                "weeks_ahead": s.weeks_ahead,
                "notify_on_cancellation": s.notify_on_cancellation,
                "notify_on_change": s.notify_on_change,
                "notify_on_new_class": s.notify_on_new_class,
                "digest_time": s.digest_time,
                "feed_token": s.feed_token,
                "ntfy_topic": s.ntfy_topic,
                "studio_url": s.studio_url,
                "last_sync_at": s.last_sync_at.isoformat() if s.last_sync_at else None,
                "last_sync_status": s.last_sync_status,
            })
        data = request.get_json(silent=True) or {}
        if "urgent_window_hours" in data:
            v = int(data["urgent_window_hours"])
            if not (1 <= v <= 168):
                return jsonify({"error": "urgent_window_hours out of range"}), 400
            s.urgent_window_hours = v
        if "duration_minutes" in data:
            v = int(data["duration_minutes"])
            if not (15 <= v <= 240):
                return jsonify({"error": "duration_minutes out of range"}), 400
            s.duration_minutes = v
        if "digest_time" in data:
            v = str(data["digest_time"])
            if not HHMM_RE.match(v):
                return jsonify({"error": "digest_time must be HH:MM"}), 400
            s.digest_time = v
        for flag in ("notify_on_cancellation", "notify_on_change",
                     "notify_on_new_class"):
            if flag in data:
                setattr(s, flag, bool(data[flag]))
        return jsonify({"ok": True})

    @app.route("/api/sync", methods=["POST"])
    def api_sync():
        s = _current_settings()
        if not s.studio_url:
            return jsonify({"error": "no studio_url set"}), 400
        http = requests.Session()
        raw_classes = fetch_schedule(
            s.studio_url,
            weeks_ahead=s.weeks_ahead or 1,
            session=g.db_session,
            http=http,
            cache_ttl_hours=config.scheduler.cache_ttl_hours,
            base_url=config.boostapp.base_url,
            user_agent=config.boostapp.user_agent,
            request_timeout=config.boostapp.request_timeout,
        )
        snapshot_count = (g.db_session.query(ClassSnapshot)
                          .filter_by(user_id=s.user_id).count())
        if not raw_classes and snapshot_count > 0:
            s.last_sync_status = "skipped:empty_scrape_with_snapshots"
            return jsonify({"error": "empty_scrape_with_snapshots",
                            "message": "הלוח חזר ריק מ-BoostApp; הסנכרון דולג כדי לא למחוק את היומן"}), 409
        enabled = {t.trainer_name for t in g.db_session.query(Trainer)
                   .filter_by(user_id=s.user_id, enabled=True).all()}
        filtered = [c for c in raw_classes if c.trainer in enabled]

        from googleapiclient.discovery import build as build_google
        def _mk(uid):
            creds = auth.load_credentials(g.db_session, config, uid)
            return build_google("calendar", "v3", credentials=creds,
                                cache_discovery=False)
        try:
            diff = sync_user(g.db_session, s.user_id, filtered,
                             duration_minutes=s.duration_minutes or 55,
                             build_service=_mk)
            process_diff(g.db_session, s.user_id, diff,
                         now=dt.datetime.now(dt.timezone.utc),
                         http=http, ntfy_server=config.ntfy.server)
            return jsonify({
                "added": len(diff.added),
                "removed": len(diff.removed),
                "changed": len(diff.changed),
            })
        except NeedsReauth:
            return jsonify({"error": "needs_reauth"}), 401

    def _render_feed_body(s: UserSettings) -> bytes:
        enabled = {t.trainer_name for t in g.db_session.query(Trainer)
                   .filter_by(user_id=s.user_id, enabled=True).all()}
        if not s.studio_url:
            return build_ics([])
        # Serve every cached week for this studio (cache lifecycle is
        # managed by the scheduler; feed is a pure read).
        rows = (g.db_session.query(ScrapeCache)
                .filter_by(studio_url=s.studio_url).all())
        items: list[ClassItem] = []
        for row in rows:
            items.extend(parse_schedule_html(row.html))
        items = [c for c in items if c.trainer in enabled]
        # Dedup by strict key in case the same class appears in two cached weeks.
        seen = set()
        deduped = []
        for c in items:
            key = (c.date, c.time, c.trainer, c.class_type, c.room)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(c)
        return build_ics(deduped,
                         duration_minutes=s.duration_minutes or 55)

    @app.route("/feed/<token>.ics")
    def feed(token: str):
        s = (g.db_session.query(UserSettings)
             .filter_by(feed_token=token).one_or_none())
        if s is None:
            abort(404)
        body = _render_feed_body(s)
        return Response(body, mimetype="text/calendar; charset=utf-8")

    @app.route("/download.ics")
    def download():
        s = _current_settings()
        body = _render_feed_body(s)
        resp = Response(body, mimetype="text/calendar; charset=utf-8")
        resp.headers["Content-Disposition"] = 'attachment; filename="studio.ics"'
        return resp

    return app


if __name__ == "__main__":  # pragma: no cover
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO)
    cfg = load_config()
    engine = get_engine(f"sqlite:///studio.db")
    init_db(engine)
    SessionLocal.configure(bind=engine)
    http = requests.Session()
    from scheduler import start as start_scheduler
    start_scheduler(cfg, SessionLocal, http)
    app = create_app(cfg, session_factory=SessionLocal)
    app.run(host=cfg.host, port=cfg.port, use_reloader=False)
