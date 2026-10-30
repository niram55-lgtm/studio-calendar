"""DB engine and Session factory."""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from models import Base

SessionLocal = sessionmaker(expire_on_commit=False)


def get_engine(url: str) -> Engine:
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
        future=True,
    )
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _enable_fk(conn, _):  # pragma: no cover - infra glue
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return engine


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)
