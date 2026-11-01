"""WSGI entry point for gunicorn (Render.com production).

Starts the APScheduler in-process only when SCHEDULER_ENABLED=1 (set this
on exactly ONE gunicorn worker — otherwise cron jobs double-fire).
"""
from __future__ import annotations

import logging
import os

import requests

from app import create_app
from config import load_config
from db import SessionLocal, get_engine, init_db

logging.basicConfig(level=logging.INFO)

cfg = load_config()
engine = get_engine(os.environ.get("DATABASE_URL", "sqlite:///studio.db"))
init_db(engine)
SessionLocal.configure(bind=engine)

if os.environ.get("SCHEDULER_ENABLED", "0") == "1":
    from scheduler import start as start_scheduler
    _http = requests.Session()
    start_scheduler(cfg, SessionLocal, _http)

app = create_app(cfg, session_factory=SessionLocal)
