"""SQLAlchemy ORM models (per spec §4)."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Integer, LargeBinary,
    String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    google_sub = Column(String, unique=True, nullable=False)
    email = Column(String, nullable=False)
    display_name = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class GoogleToken(Base):
    __tablename__ = "google_tokens"
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     primary_key=True)
    encrypted_access = Column(LargeBinary, nullable=False)
    encrypted_refresh = Column(LargeBinary, nullable=False)
    expiry = Column(DateTime(timezone=True), nullable=False)
    calendar_id = Column(String)


class UserSettings(Base):
    __tablename__ = "user_settings"
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     primary_key=True)
    studio_url = Column(String)
    studio_display_name = Column(String, default="סטודיו נופר")
    feed_token = Column(String, unique=True, nullable=False)
    ntfy_topic = Column(String, unique=True, nullable=False)
    urgent_window_hours = Column(Integer, default=24)
    duration_minutes = Column(Integer, default=55)
    weeks_ahead = Column(Integer, default=1)
    notify_on_cancellation = Column(Boolean, default=True)
    notify_on_change = Column(Boolean, default=True)
    notify_on_new_class = Column(Boolean, default=False)
    digest_time = Column(String, default="06:30")
    last_sync_at = Column(DateTime(timezone=True))
    last_sync_status = Column(String)


class Trainer(Base):
    __tablename__ = "trainers"
    __table_args__ = (UniqueConstraint("user_id", "trainer_name"),)
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False)
    trainer_name = Column(String, nullable=False)
    enabled = Column(Boolean, default=True)


class ClassSnapshot(Base):
    __tablename__ = "class_snapshots"
    __table_args__ = (UniqueConstraint("user_id", "loose_uid"),)
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False)
    loose_uid = Column(String, nullable=False)
    strict_uid = Column(String, nullable=False)
    class_date = Column(Date, nullable=False)
    class_time = Column(String, nullable=False)  # "HH:MM"
    trainer = Column(String, nullable=False)
    class_type = Column(String, nullable=False)
    room = Column(String, nullable=False)
    google_event_id = Column(String)
    synced_at = Column(DateTime(timezone=True), server_default=func.now())


class PendingDigest(Base):
    __tablename__ = "pending_digest"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False)
    change_type = Column(String, nullable=False)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class NotificationsLog(Base):
    __tablename__ = "notifications_log"
    __table_args__ = (UniqueConstraint("user_id", "loose_uid", "change_type"),)
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False)
    loose_uid = Column(String, nullable=False)
    change_type = Column(String, nullable=False)
    sent_at = Column(DateTime(timezone=True), server_default=func.now())


class ScrapeCache(Base):
    __tablename__ = "scrape_cache"
    studio_url = Column(String, primary_key=True)
    week_start = Column(Date, primary_key=True)
    html = Column(Text, nullable=False)
    fetched_at = Column(DateTime(timezone=True), server_default=func.now())
