from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, BigInteger, String, Text, create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoginSession(Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Execution(Base):
    __tablename__ = "executions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(32), default="generation")
    category: Mapped[str] = mapped_column(String(32), default="image")
    operation: Mapped[str] = mapped_column(String(64), default="image.generate")
    workflow: Mapped[str] = mapped_column(String(128))
    upstream_job_id: Mapped[str | None] = mapped_column(String(256))
    request_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    last_known_status: Mapped[str] = mapped_column(String(32), default="submitting")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (Index("ix_asset_execution_upstream", "execution_id", "upstream_asset_id", unique=True),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(32), default="generation")
    upstream_asset_id: Mapped[str] = mapped_column(String(256))
    storage_provider: Mapped[str] = mapped_column(String(64), default="generation-mcp")
    storage_locator: Mapped[str] = mapped_column(String(256))
    storage_path: Mapped[str | None] = mapped_column(Text)
    original_filename: Mapped[str | None] = mapped_column(String(256))
    display_name: Mapped[str] = mapped_column(String(128))
    media_kind: Mapped[str] = mapped_column(String(32))
    mime_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    checksum: Mapped[str | None] = mapped_column(String(64))
    thumbnail_locator: Mapped[str | None] = mapped_column(String(64))
    availability: Mapped[str] = mapped_column(String(32), default="available")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


def make_session_factory():
    dsn = os.environ.get("STUDIO_DATABASE_URL")
    if not dsn:
        raise RuntimeError("STUDIO_DATABASE_URL must be configured")
    return sessionmaker(create_engine(dsn, pool_pre_ping=True), expire_on_commit=False)
