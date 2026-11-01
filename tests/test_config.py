"""Tests for config loader."""
from __future__ import annotations

import os
import pytest

from config import load_config


def test_load_config_reads_yaml_defaults():
    cfg = load_config("config.example.yaml")
    assert cfg.defaults.class_duration_minutes == 55
    assert cfg.defaults.weeks_ahead == 1
    assert cfg.defaults.urgent_window_hours == 24
    assert cfg.scheduler.daily_sync_hour == 6
    assert cfg.scheduler.daily_sync_minute == 0


def test_load_config_overrides_base_url_from_env(monkeypatch):
    monkeypatch.setenv("BASE_URL", "https://x.example")
    cfg = load_config("config.example.yaml")
    assert cfg.base_url == "https://x.example"


def test_load_config_rejects_missing_fernet_key(monkeypatch):
    # Prevent load_dotenv from repopulating env from the project's .env.
    monkeypatch.setattr("config.load_dotenv", lambda *a, **kw: None)
    monkeypatch.delenv("FERNET_KEY", raising=False)
    with pytest.raises(RuntimeError):
        load_config("config.example.yaml")
