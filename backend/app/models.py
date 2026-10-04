from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, TypeDecorator, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class UTCDateTime(TypeDecorator):
    """SQLite drops tzinfo; store naive UTC and always hand back aware UTC."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None else None


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    rating: Mapped[float] = mapped_column(Float, default=1000.0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class AuthToken(Base):
    __tablename__ = "auth_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    user: Mapped[User] = relationship()


class Challenge(Base):
    __tablename__ = "challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    difficulty: Mapped[str] = mapped_column(String(16), default="easy")
    current_version_id: Mapped[int | None] = mapped_column(ForeignKey("challenge_versions.id", use_alter=True))

    current_version: Mapped["ChallengeVersion | None"] = relationship(foreign_keys=[current_version_id], post_update=True)


class ChallengeVersion(Base):
    """An immutable challenge contract: both duel players are scored against exactly this."""

    __tablename__ = "challenge_versions"
    __table_args__ = (UniqueConstraint("challenge_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    challenge_id: Mapped[int] = mapped_column(ForeignKey("challenges.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    duration_s: Mapped[float] = mapped_column(Float)
    song_path: Mapped[str] = mapped_column(String(512))
    song_sha256: Mapped[str] = mapped_column(String(64))
    song_mime: Mapped[str] = mapped_column(String(64), default="audio/mpeg")
    segment_start_s: Mapped[float] = mapped_column(Float, default=0.0)
    segment_end_s: Mapped[float] = mapped_column(Float)
    reference_pose_path: Mapped[str] = mapped_column(String(512))
    reference_sha256: Mapped[str] = mapped_column(String(64))
    practice_video_path: Mapped[str | None] = mapped_column(String(512))
    practice_reference_allowed: Mapped[bool] = mapped_column(Boolean, default=True)  # stick-figure preview in practice
    pose_model: Mapped[str] = mapped_column(String(128))
    normalizer_version: Mapped[str] = mapped_column(String(32))
    mirror_policy: Mapped[str] = mapped_column(String(16), default="none")
    spec: Mapped[dict] = mapped_column(JSON)  # ChallengeSpec.to_dict(): events, windows, thresholds, weights
    license_note: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    challenge: Mapped[Challenge] = relationship(foreign_keys=[challenge_id])


class Duel(Base):
    __tablename__ = "duels"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    challenge_version_id: Mapped[int] = mapped_column(ForeignKey("challenge_versions.id"), index=True)
    challenger_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    opponent_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # invited -> awaiting_attempts -> analyzing -> finalized; or declined / expired
    status: Mapped[str] = mapped_column(String(24), default="invited", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    challenger_attempt_id: Mapped[str | None] = mapped_column(ForeignKey("attempts.id", use_alter=True))
    opponent_attempt_id: Mapped[str | None] = mapped_column(ForeignKey("attempts.id", use_alter=True))
    winner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    is_draw: Mapped[bool] = mapped_column(Boolean, default=False)
    challenger_rating_delta: Mapped[float | None] = mapped_column(Float)
    opponent_rating_delta: Mapped[float | None] = mapped_column(Float)
    finalized_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)

    challenge_version: Mapped[ChallengeVersion] = relationship()
    challenger: Mapped[User] = relationship(foreign_keys=[challenger_id])
    opponent: Mapped[User] = relationship(foreign_keys=[opponent_id])


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    challenge_version_id: Mapped[int] = mapped_column(ForeignKey("challenge_versions.id"), index=True)
    duel_id: Mapped[str | None] = mapped_column(ForeignKey("duels.id"), index=True)
    mode: Mapped[str] = mapped_column(String(16))  # practice | official
    # started -> queued -> processing -> scored | invalid | technical_failure; or aborted
    status: Mapped[str] = mapped_column(String(24), default="started", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    uploaded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    analyzed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    song_offset_ms: Mapped[float | None] = mapped_column(Float)
    recording_duration_ms: Mapped[float | None] = mapped_column(Float)
    video_path: Mapped[str | None] = mapped_column(String(512))
    pose_path: Mapped[str | None] = mapped_column(String(512))
    pose_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    analysis_retries: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)

    invalid_reason: Mapped[str | None] = mapped_column(String(32))
    invalid_detail: Mapped[str | None] = mapped_column(Text)
    pose_score: Mapped[float | None] = mapped_column(Float)
    timing_score: Mapped[float | None] = mapped_column(Float)
    dynamics_score: Mapped[float | None] = mapped_column(Float)
    flow_score: Mapped[float | None] = mapped_column(Float)
    total_score: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict | None] = mapped_column(JSON)  # coverage, events, timeline, feedback
    analysis_version: Mapped[str | None] = mapped_column(String(32))

    user: Mapped[User] = relationship()
    challenge_version: Mapped[ChallengeVersion] = relationship()
