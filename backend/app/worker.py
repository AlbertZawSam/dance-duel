"""Queued post-attempt analysis, plus privacy cleanup and duel expiry."""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from . import duels
from .config import Settings
from .db import Database
from .models import Attempt, utcnow
from .pose import ExtractionError, PoseExtractor
from .scoring import ChallengeSpec, PoseSequence, score_attempt

log = logging.getLogger("danceduel.worker")


def _delete(path: str | None) -> None:
    if path:
        Path(path).unlink(missing_ok=True)


def analyze_attempt(db, attempt_id: str, extractor: PoseExtractor, settings: Settings, now: datetime | None = None) -> str:
    """Analyze one queued attempt. Returns the resulting status."""
    now = now or utcnow()
    attempt = db.get(Attempt, attempt_id)
    if attempt is None or attempt.status not in ("queued", "processing"):
        return attempt.status if attempt else "missing"
    attempt.status = "processing"
    db.commit()

    version = attempt.challenge_version
    spec = ChallengeSpec.from_dict(version.spec)
    try:
        reference = PoseSequence.load(settings.protected_dir / version.reference_pose_path)
        player = extractor.extract(
            Path(attempt.video_path),
            song_offset_s=(attempt.song_offset_ms or 0.0) / 1000.0,
            duration_s=version.duration_s,
            fps=spec.config.fps,
            recording_duration_s=(attempt.recording_duration_ms or 0.0) / 1000.0 or None,
        )
        result = score_attempt(reference, player, spec)
    except ExtractionError as exc:
        attempt.status = "invalid"
        attempt.invalid_reason = "unreadable_recording"
        attempt.invalid_detail = f"The recording could not be read ({exc}). Please record again."
        _finish(db, attempt, now, settings)
        return attempt.status
    except Exception as exc:  # model or I/O failure: bounded retry, then technical failure
        log.exception("analysis failed for %s", attempt_id)
        attempt.analysis_retries += 1
        attempt.error = repr(exc)[:2000]
        if attempt.analysis_retries <= settings.max_analysis_retries:
            attempt.status = "queued"
            db.commit()
            return "retry"
        attempt.status = "technical_failure"
        _finish(db, attempt, now, settings)
        return attempt.status

    pose_file = settings.poses_dir / f"{attempt.id}.npz"
    player.save(pose_file)
    attempt.pose_path = str(pose_file)
    attempt.pose_expires_at = now + timedelta(days=settings.pose_retention_days)
    attempt.analysis_version = result.analysis_version
    if result.valid:
        attempt.status = "scored"
        attempt.pose_score, attempt.timing_score = result.pose, result.timing
        attempt.dynamics_score, attempt.flow_score = result.dynamics, result.flow
        attempt.total_score = result.total
    else:
        attempt.status = "invalid"
        attempt.invalid_reason, attempt.invalid_detail = result.invalid_reason, result.invalid_detail
    attempt.details = {
        "coverage": result.coverage,
        "events": [e.__dict__ for e in result.events],
        "timeline": result.timeline,
        "feedback": result.feedback,
    }
    _finish(db, attempt, now, settings)
    return attempt.status


def _finish(db, attempt: Attempt, now: datetime, settings: Settings) -> None:
    # Privacy default: the raw video is deleted as soon as analysis ends, success or not.
    _delete(attempt.video_path)
    attempt.video_path = None
    attempt.analyzed_at = now
    duels.on_attempt_finished(db, attempt, now, settings)
    db.commit()


def _fail_technical(db, attempt_id: str, error: str, settings: Settings) -> None:
    attempt = db.get(Attempt, attempt_id)
    if attempt is None or attempt.status not in ("queued", "processing"):
        return
    attempt.status, attempt.error = "technical_failure", error[:2000]
    _finish(db, attempt, utcnow(), settings)


def cleanup(db, settings: Settings, now: datetime | None = None) -> dict:
    now = now or utcnow()
    stale = now - timedelta(hours=settings.raw_video_retention_hours)
    videos = 0
    for a in db.scalars(select(Attempt).where(Attempt.video_path.is_not(None), Attempt.uploaded_at < stale)):
        _delete(a.video_path)
        a.video_path = None
        if a.status in ("queued", "processing"):
            a.status, a.error = "technical_failure", "not analyzed within the retention window"
            duels.on_attempt_finished(db, a, now, settings)
        videos += 1
    poses = 0
    for a in db.scalars(select(Attempt).where(Attempt.pose_path.is_not(None), Attempt.pose_expires_at < now)):
        _delete(a.pose_path)
        a.pose_path = None
        poses += 1
    expired = duels.expire_due(db, now)
    db.commit()
    return {"videos_deleted": videos, "poses_deleted": poses, "duels_expired": expired}


class AnalysisWorker:
    def __init__(self, database: Database, settings: Settings, extractor_factory: Callable[[], PoseExtractor]):
        self.database = database
        self.settings = settings
        self._factory = extractor_factory
        self._extractor: PoseExtractor | None = None
        self._queue: queue.Queue[str] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def extractor(self) -> PoseExtractor:
        if self._extractor is None:
            self._extractor = self._factory()  # loads model weights on first use
        return self._extractor

    def start(self) -> None:
        with self.database.session() as db:
            for a in db.scalars(select(Attempt).where(Attempt.status.in_(("queued", "processing")))):
                a.status = "queued"
                self._queue.put(a.id)
            db.commit()
        self._thread = threading.Thread(target=self._run, name="analysis-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def submit(self, attempt_id: str) -> None:
        self._queue.put(attempt_id)

    def _run(self) -> None:
        last_cleanup = datetime.min.replace(tzinfo=utcnow().tzinfo)
        while not self._stop.is_set():
            if utcnow() - last_cleanup > timedelta(minutes=10):
                with self.database.session() as db:
                    log.info("cleanup: %s", cleanup(db, self.settings))
                last_cleanup = utcnow()
            try:
                attempt_id = self._queue.get(timeout=5)
            except queue.Empty:
                continue
            with self.database.session() as db:
                try:
                    extractor = self.extractor
                except Exception as exc:  # pose model unavailable: fail visibly, not silently
                    log.exception("pose model failed to load")
                    _fail_technical(db, attempt_id, f"pose model unavailable: {exc!r}", self.settings)
                    continue
                try:
                    status = analyze_attempt(db, attempt_id, extractor, self.settings)
                except Exception:
                    log.exception("worker crashed on %s", attempt_id)
                    continue
            if status == "retry":
                self._queue.put(attempt_id)
