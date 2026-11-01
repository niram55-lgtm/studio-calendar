"""Config loader: YAML + env overlay."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv


@dataclass
class Defaults:
    timezone: str
    class_duration_minutes: int
    weeks_ahead: int
    urgent_window_hours: int
    digest_time: str


@dataclass
class SchedulerCfg:
    daily_sync_hour: int
    daily_sync_minute: int
    cache_ttl_hours: int


@dataclass
class BoostAppCfg:
    base_url: str
    user_agent: str
    request_timeout: int


@dataclass
class NtfyCfg:
    server: str


@dataclass
class Config:
    deployment_mode: Literal["local", "cloud"]
    base_url: str
    host: str
    port: int
    secret_key: str
    fernet_key: bytes
    google_client_id: str
    google_client_secret: str
    defaults: Defaults
    scheduler: SchedulerCfg
    boostapp: BoostAppCfg
    ntfy: NtfyCfg


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def load_config(path: str | None = None) -> Config:
    """Load YAML config, overlay env vars.

    Raises RuntimeError on missing SECRET_KEY, FERNET_KEY,
    GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET.
    """
    load_dotenv(override=False)

    cfg_path = Path(path or "config.yaml")
    with cfg_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    secret_key = _require_env("SECRET_KEY")
    fernet_key_str = _require_env("FERNET_KEY")
    client_id = _require_env("GOOGLE_CLIENT_ID")
    client_secret = _require_env("GOOGLE_CLIENT_SECRET")

    base_url = os.environ.get("BASE_URL", raw["base_url"])
    deployment_mode = os.environ.get("DEPLOYMENT_MODE", raw["deployment_mode"])

    return Config(
        deployment_mode=deployment_mode,
        base_url=base_url,
        host=raw["host"],
        port=int(raw["port"]),
        secret_key=secret_key,
        fernet_key=fernet_key_str.encode(),
        google_client_id=client_id,
        google_client_secret=client_secret,
        defaults=Defaults(**raw["defaults"]),
        scheduler=SchedulerCfg(**raw["scheduler"]),
        boostapp=BoostAppCfg(**raw["boostapp"]),
        ntfy=NtfyCfg(**raw["ntfy"]),
    )
