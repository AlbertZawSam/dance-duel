from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import auth, dashboard, duels
from .config import Settings
from .models import Attempt, Challenge, ChallengeVersion, Duel, User
from .scoring import PoseSequence

router = APIRouter(prefix="/api")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")


# --- dependencies -----------------------------------------------------------


def get_db(request: Request):
    yield from request.app.state.database.dependency()


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def now(request: Request) -> datetime:
    return request.app.state.clock()


DB = Annotated[Session, Depends(get_db)]
Cfg = Annotated[Settings, Depends(get_settings)]
Now = Annotated[datetime, Depends(now)]


def current_user(db: DB, authorization: Annotated[str | None, Header()] = None) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Sign in required.")
    user = auth.user_for_token(db, authorization[7:].strip())
    if user is None:
        raise HTTPException(401, "Your session has expired. Please sign in again.")
    return user


Me = Annotated[User, Depends(current_user)]


def _duel_error(exc: duels.DuelError) -> HTTPException:
    return HTTPException(exc.status_code, exc.message)


# --- serializers -------------------------------------------------------------


def user_out(u: User) -> dict:
    return {"id": u.id, "username": u.username, "rating": round(u.rating, 1)}


def version_out(v: ChallengeVersion) -> dict:
    return {
        "id": v.id,
        "version": v.version,
        "duration_s": v.duration_s,
        "mirror_policy": v.mirror_policy,
        "has_practice_video": bool(v.practice_video_path),
        "has_practice_reference": v.practice_reference_allowed,
        "pose_model": v.pose_model,
        "license_note": v.license_note,
        "published_at": v.published_at.isoformat(),
    }


def challenge_out(c: Challenge) -> dict:
    return {
        "id": c.id,
        "slug": c.slug,
        "title": c.title,
        "description": c.description,
        "difficulty": c.difficulty,
        "current_version": version_out(c.current_version) if c.current_version else None,
    }


def _result(a: Attempt, include_feedback: bool) -> dict:
    out = {
        "total": a.total_score,
        "pose": a.pose_score,
        "timing": a.timing_score,
        "dynamics": a.dynamics_score,
        "flow": a.flow_score,
        "analysis_version": a.analysis_version,
    }
    if include_feedback and a.details:
        out.update({k: a.details.get(k) for k in ("coverage", "events", "timeline", "feedback")})
    return out


def attempt_out(db: Session, a: Attempt, viewer: User) -> dict:
    duel = db.get(Duel, a.duel_id) if a.duel_id else None
    revealed = a.mode == "practice" or (duel is not None and duel.status == "finalized")
    own = a.user_id == viewer.id
    out = {
        "id": a.id,
        "mode": a.mode,
        "status": a.status,
        "challenge_version_id": a.challenge_version_id,
        "challenge_slug": a.challenge_version.challenge.slug,
        "duel_id": a.duel_id,
        "created_at": a.created_at.isoformat(),
        "result_hidden": a.status == "scored" and not revealed,
        "result": None,
    }
    if own and a.status == "invalid":
        out["invalid_reason"], out["invalid_detail"] = a.invalid_reason, a.invalid_detail
        out["coverage"] = (a.details or {}).get("coverage")
    if a.status == "scored" and revealed:
        out["result"] = _result(a, include_feedback=own)
    return out


def duel_out(db: Session, d: Duel, viewer: User, settings: Settings) -> dict:
    me_first = viewer.id == d.challenger_id
    opp_id = d.opponent_id if me_first else d.challenger_id

    def progress(uid: int) -> str:
        if duels.slot_attempt_id(d, uid):
            return "done"
        latest = db.scalar(
            select(Attempt).where(Attempt.duel_id == d.id, Attempt.user_id == uid).order_by(Attempt.created_at.desc())
        )
        return latest.status if latest else "not_started"

    used = db.scalar(
        select(func.count()).where(
            Attempt.duel_id == d.id, Attempt.user_id == viewer.id, Attempt.status.in_(duels.UPLOADED_ATTEMPT)
        )
    )
    out = {
        "id": d.id,
        "status": d.status,
        "challenge": {"id": d.challenge_version.challenge.id, "slug": d.challenge_version.challenge.slug, "title": d.challenge_version.challenge.title},
        "challenge_version": d.challenge_version.version,
        "challenge_version_id": d.challenge_version_id,
        "challenger": user_out(d.challenger),
        "opponent": user_out(d.opponent),
        "role": "challenger" if me_first else "opponent",
        "created_at": d.created_at.isoformat(),
        "expires_at": d.expires_at.isoformat(),
        "my_progress": progress(viewer.id),
        "opponent_progress": progress(opp_id),
        "attempts_left": max(0, settings.max_official_attempts_per_duel - used),
        "outcome": None,
    }
    if d.status == "finalized":
        a, b = db.get(Attempt, d.challenger_attempt_id), db.get(Attempt, d.opponent_attempt_id)
        out["outcome"] = {
            "winner": None if d.is_draw else (d.challenger.username if d.winner_id == d.challenger_id else d.opponent.username),
            "is_draw": d.is_draw,
            "challenger": {"attempt_id": a.id, **_result(a, include_feedback=False), "rating_delta": d.challenger_rating_delta},
            "opponent": {"attempt_id": b.id, **_result(b, include_feedback=False), "rating_delta": d.opponent_rating_delta},
            "finalized_at": d.finalized_at.isoformat(),
        }
    return out


# --- auth -------------------------------------------------------------------


class Credentials(BaseModel):
    username: str
    password: str = Field(min_length=8, max_length=128)


@router.post("/auth/register", status_code=201)
def register(body: Credentials, db: DB, settings: Cfg):
    if not USERNAME_RE.match(body.username):
        raise HTTPException(422, "Usernames use 3-32 letters, digits or underscores.")
    if db.scalar(select(User).where(func.lower(User.username) == body.username.lower())):
        raise HTTPException(409, "That username is taken.")
    user = User(username=body.username, password_hash=auth.hash_password(body.password), rating=settings.initial_rating)
    db.add(user)
    db.commit()
    return {"token": auth.issue_token(db, user), "user": user_out(user)}


@router.post("/auth/login")
def login(body: Credentials, db: DB):
    user = db.scalar(select(User).where(func.lower(User.username) == body.username.lower()))
    if user is None or not auth.verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Wrong username or password.")
    return {"token": auth.issue_token(db, user), "user": user_out(user)}


@router.post("/auth/logout", status_code=204)
def logout(db: DB, me: Me, authorization: Annotated[str, Header()]):
    auth.revoke_token(db, authorization[7:].strip())


@router.get("/me")
def get_me(me: Me):
    return user_out(me)


@router.get("/users")
def search_users(db: DB, me: Me, q: str = ""):
    rows = db.scalars(
        select(User).where(User.username.ilike(f"%{q}%"), User.id != me.id).order_by(User.username).limit(10)
    )
    return [user_out(u) for u in rows]


@router.get("/users/{username}/record")
def get_record(username: str, db: DB, me: Me):
    user = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if user is None:
        raise HTTPException(404, "No such user.")
    return dashboard.user_record(db, user)


# --- challenges ---------------------------------------------------------------


@router.get("/challenges")
def list_challenges(db: DB, me: Me):
    rows = db.scalars(select(Challenge).where(Challenge.current_version_id.is_not(None)).order_by(Challenge.title))
    return [challenge_out(c) for c in rows]


@router.get("/challenges/{slug}")
def get_challenge(slug: str, db: DB, me: Me):
    c = db.scalar(select(Challenge).where(Challenge.slug == slug))
    if c is None or c.current_version is None:
        raise HTTPException(404, "No such challenge.")
    return challenge_out(c)


def _protected_file(settings: Settings, rel: str | None) -> Path:
    if not rel:
        raise HTTPException(404, "Not available.")
    path = (settings.protected_dir / rel).resolve()
    if settings.protected_dir.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "Not available.")
    return path


@router.get("/challenge-versions/{version_id}/audio")
def get_audio(version_id: int, db: DB, me: Me, settings: Cfg):
    v = db.get(ChallengeVersion, version_id)
    if v is None:
        raise HTTPException(404, "No such challenge version.")
    return FileResponse(_protected_file(settings, v.song_path), media_type=v.song_mime)


@router.get("/challenge-versions/{version_id}/practice-video")
def get_practice_video(version_id: int, db: DB, me: Me, settings: Cfg):
    """Practice only. The official attempt UI never requests this."""
    v = db.get(ChallengeVersion, version_id)
    if v is None:
        raise HTTPException(404, "No such challenge version.")
    return FileResponse(_protected_file(settings, v.practice_video_path))


@router.get("/challenge-versions/{version_id}/practice-pose")
def get_practice_pose(version_id: int, db: DB, me: Me, settings: Cfg):
    """Practice only: the reference skeleton, normalized to a unit box, for a stick-figure guide."""
    v = db.get(ChallengeVersion, version_id)
    if v is None or not v.practice_reference_allowed:
        raise HTTPException(404, "Not available.")
    seq = PoseSequence.load(_protected_file(settings, v.reference_pose_path))
    ok = seq.scores >= 0.3
    pts = seq.keypoints[ok] if ok.any() else seq.keypoints.reshape(-1, 2)
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    span = float(max(hi - lo)) or 1.0
    norm = (seq.keypoints - lo) / span
    frames = [
        [[round(float(x), 4), round(float(y), 4), round(float(c), 2)] for (x, y), c in zip(kp, sc)]
        for kp, sc in zip(norm, seq.scores)
    ]
    return {"fps": seq.fps, "aspect": float((hi - lo)[0] / span), "frames": frames}


# --- camera preflight ----------------------------------------------------------


@router.post("/preflight")
async def preflight(request: Request, me: Me, image: UploadFile = File(...)):
    data = await image.read(5 * 1024 * 1024 + 1)
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, "Preview image too large.")
    try:
        extractor = request.app.state.worker.extractor
        info = extractor.check_frame(data)
    except Exception as exc:  # model missing or unreadable image
        raise HTTPException(503, f"Camera check unavailable: {exc}") from exc
    checks = {
        "one_person": info["people"] == 1,
        "full_body": info["full_body"],
        "lighting": info["brightness"] >= 60,
    }
    return {"ok": all(checks.values()), "checks": checks, **info}


# --- attempts --------------------------------------------------------------------


class AttemptCreate(BaseModel):
    challenge_version_id: int
    mode: Literal["practice", "official"]
    duel_id: str | None = None


@router.post("/attempts", status_code=201)
def create_attempt(body: AttemptCreate, db: DB, me: Me, settings: Cfg, at: Now):
    version = db.get(ChallengeVersion, body.challenge_version_id)
    if version is None:
        raise HTTPException(404, "No such challenge version.")
    if body.mode == "official":
        duel = db.get(Duel, body.duel_id) if body.duel_id else None
        if duel is None:
            raise HTTPException(400, "Official attempts belong to a duel.")
        if duel.challenge_version_id != version.id:
            raise HTTPException(409, "This duel is locked to a different challenge version.")
        try:
            duels.check_can_start_official(db, duel, me, at, settings)
        except duels.DuelError as exc:
            raise _duel_error(exc) from exc
    elif body.duel_id:
        raise HTTPException(400, "Practice attempts never count towards a duel.")

    # An attempt that was started but never uploaded is superseded.
    for old in db.scalars(
        select(Attempt).where(Attempt.user_id == me.id, Attempt.status == "started", Attempt.duel_id == body.duel_id)
    ):
        old.status = "aborted"
    attempt = Attempt(user_id=me.id, challenge_version_id=version.id, duel_id=body.duel_id, mode=body.mode, created_at=at)
    db.add(attempt)
    db.commit()
    return {**attempt_out(db, attempt, me), "duration_s": version.duration_s}


@router.get("/attempts")
def list_attempts(db: DB, me: Me, mode: str | None = None, limit: int = 20):
    q = select(Attempt).where(Attempt.user_id == me.id, Attempt.status != "aborted")
    if mode:
        q = q.where(Attempt.mode == mode)
    rows = db.scalars(q.order_by(Attempt.created_at.desc()).limit(min(limit, 100)))
    return [attempt_out(db, a, me) for a in rows]


def _own_attempt(db: Session, attempt_id: str, me: User) -> Attempt:
    a = db.get(Attempt, attempt_id)
    if a is None or a.user_id != me.id:
        raise HTTPException(404, "No such attempt.")
    return a


@router.get("/attempts/{attempt_id}")
def get_attempt(attempt_id: str, db: DB, me: Me):
    a = db.get(Attempt, attempt_id)
    if a is None:
        raise HTTPException(404, "No such attempt.")
    if a.user_id != me.id:
        duel = db.get(Duel, a.duel_id) if a.duel_id else None
        if duel is None or me.id not in (duel.challenger_id, duel.opponent_id):
            raise HTTPException(404, "No such attempt.")
    return attempt_out(db, a, me)


@router.post("/attempts/{attempt_id}/abort")
def abort_attempt(attempt_id: str, db: DB, me: Me):
    a = _own_attempt(db, attempt_id, me)
    if a.status == "started":
        a.status = "aborted"
        db.commit()
    return attempt_out(db, a, me)


@router.put("/attempts/{attempt_id}/recording", status_code=202)
def upload_recording(
    attempt_id: str,
    request: Request,
    db: DB,
    me: Me,
    settings: Cfg,
    at: Now,
    video: UploadFile = File(...),
    song_offset_ms: float = Form(..., ge=0, le=15000),
    recording_duration_ms: float | None = Form(None, ge=0),
):
    a = _own_attempt(db, attempt_id, me)
    if a.status != "started":
        # Idempotent: a network retry with the same attempt ID returns the existing submission.
        return attempt_out(db, a, me)
    content_type = (video.content_type or "").split(";")[0].strip()
    if content_type not in settings.allowed_video_types:
        raise HTTPException(415, f"Unsupported recording type {content_type!r}.")
    max_ms = (a.challenge_version.duration_s + 30) * 1000
    if recording_duration_ms is not None and recording_duration_ms > max_ms:
        raise HTTPException(422, "The recording is much longer than the challenge.")
    if a.duel_id:
        duel = db.get(Duel, a.duel_id)
        if duel.status not in ("awaiting_attempts", "analyzing") or at >= duel.expires_at:
            raise HTTPException(409, f"This duel is {duel.status if duel.status != 'awaiting_attempts' else 'expired'}.")

    ext = {"video/webm": ".webm", "video/mp4": ".mp4", "video/quicktime": ".mov"}[content_type]
    dest = settings.uploads_dir / f"{a.id}{ext}"
    written = 0
    with dest.open("wb") as fh:
        while chunk := video.file.read(1024 * 1024):
            written += len(chunk)
            if written > settings.max_upload_bytes:
                fh.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "The recording is too large.")
            fh.write(chunk)

    a.video_path = str(dest)
    a.song_offset_ms = song_offset_ms
    a.recording_duration_ms = recording_duration_ms
    a.uploaded_at = at
    a.status = "queued"
    if a.duel_id:
        duels.refresh_status(db, db.get(Duel, a.duel_id), at)
    db.commit()
    request.app.state.worker.submit(a.id)
    return attempt_out(db, a, me)


# --- duels --------------------------------------------------------------------------


class DuelCreate(BaseModel):
    opponent_username: str
    challenge_slug: str


@router.post("/duels", status_code=201)
def create_duel(body: DuelCreate, db: DB, me: Me, settings: Cfg, at: Now):
    opponent = db.scalar(select(User).where(func.lower(User.username) == body.opponent_username.lower()))
    if opponent is None:
        raise HTTPException(404, "No such player.")
    challenge = db.scalar(select(Challenge).where(Challenge.slug == body.challenge_slug))
    if challenge is None or challenge.current_version is None:
        raise HTTPException(404, "No such challenge.")
    try:
        duel = duels.create_duel(db, me, opponent, challenge.current_version, at, settings)
    except duels.DuelError as exc:
        raise _duel_error(exc) from exc
    db.commit()
    return duel_out(db, duel, me, settings)


@router.get("/duels")
def list_duels(db: DB, me: Me, settings: Cfg, at: Now):
    duels.expire_due(db, at)
    db.commit()
    rows = db.scalars(
        select(Duel).where(or_(Duel.challenger_id == me.id, Duel.opponent_id == me.id)).order_by(Duel.created_at.desc()).limit(50)
    )
    return [duel_out(db, d, me, settings) for d in rows]


def _my_duel(db: Session, duel_id: str, me: User) -> Duel:
    d = db.get(Duel, duel_id)
    if d is None or me.id not in (d.challenger_id, d.opponent_id):
        raise HTTPException(404, "No such duel.")
    return d


@router.get("/duels/{duel_id}")
def get_duel(duel_id: str, db: DB, me: Me, settings: Cfg, at: Now):
    duels.expire_due(db, at)
    db.commit()
    return duel_out(db, _my_duel(db, duel_id, me), me, settings)


@router.post("/duels/{duel_id}/accept")
def accept_duel(duel_id: str, db: DB, me: Me, settings: Cfg, at: Now):
    d = _my_duel(db, duel_id, me)
    try:
        duels.accept(db, d, me, at)
    except duels.DuelError as exc:
        raise _duel_error(exc) from exc
    db.commit()
    return duel_out(db, d, me, settings)


@router.post("/duels/{duel_id}/decline")
def decline_duel(duel_id: str, db: DB, me: Me, settings: Cfg, at: Now):
    d = _my_duel(db, duel_id, me)
    try:
        duels.decline(db, d, me, at)
    except duels.DuelError as exc:
        raise _duel_error(exc) from exc
    db.commit()
    return duel_out(db, d, me, settings)


# --- dashboard ------------------------------------------------------------------------


@router.get("/dashboard/weekly")
def weekly_dashboard(db: DB, me: Me, settings: Cfg, at: Now):
    start, end = dashboard.week_bounds(at, settings.timezone)
    return {
        "timezone": settings.timezone,
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "min_duels_for_best_users": dashboard.MIN_WEEKLY_DUELS,
        "trending_challenges": dashboard.trending_challenges(db, start, end),
        "best_users": dashboard.best_users(db, start, end),
    }
