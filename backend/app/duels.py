"""Asynchronous duel contract, outcome rule and Dance Rating.

invited -> awaiting_attempts -> analyzing -> finalized
          (declined)          (expired: no rating change)
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .config import Settings
from .models import Attempt, ChallengeVersion, Duel, User

OPEN_STATES = ("invited", "awaiting_attempts", "analyzing")
PENDING_ATTEMPT = ("queued", "processing")
UPLOADED_ATTEMPT = ("queued", "processing", "scored", "invalid", "technical_failure")


class DuelError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def expected_score(rating: float, opponent_rating: float) -> float:
    return 1.0 / (1.0 + 10 ** ((opponent_rating - rating) / 400.0))


def rating_delta(rating: float, opponent_rating: float, outcome: float, k: float) -> float:
    """Elo-style update: outcome is 1 (win), 0.5 (draw) or 0 (loss)."""
    return k * (outcome - expected_score(rating, opponent_rating))


def create_duel(db: Session, challenger: User, opponent: User, version: ChallengeVersion, now: datetime, settings: Settings) -> Duel:
    if challenger.id == opponent.id:
        raise DuelError("You cannot duel yourself.")
    duel = Duel(
        challenge_version_id=version.id,
        challenger_id=challenger.id,
        opponent_id=opponent.id,
        status="invited",
        created_at=now,
        expires_at=now + timedelta(hours=settings.duel_expiry_hours),
    )
    db.add(duel)
    db.flush()
    return duel


def _ensure_open(duel: Duel, now: datetime) -> None:
    if duel.status not in OPEN_STATES:
        raise DuelError(f"This duel is {duel.status}.", 409)
    if now >= duel.expires_at:
        raise DuelError("This duel has expired.", 409)


def accept(db: Session, duel: Duel, user: User, now: datetime) -> Duel:
    if user.id != duel.opponent_id:
        raise DuelError("Only the invited player can accept.", 403)
    _ensure_open(duel, now)
    if duel.status != "invited":
        raise DuelError("This duel was already accepted.", 409)
    duel.status = "awaiting_attempts"
    return duel


def decline(db: Session, duel: Duel, user: User, now: datetime) -> Duel:
    if user.id != duel.opponent_id:
        raise DuelError("Only the invited player can decline.", 403)
    if duel.status != "invited":
        raise DuelError(f"This duel is {duel.status}.", 409)
    duel.status = "declined"
    return duel


def slot_attempt_id(duel: Duel, user_id: int) -> str | None:
    return duel.challenger_attempt_id if user_id == duel.challenger_id else duel.opponent_attempt_id


def check_can_start_official(db: Session, duel: Duel, user: User, now: datetime, settings: Settings) -> None:
    if user.id not in (duel.challenger_id, duel.opponent_id):
        raise DuelError("You are not part of this duel.", 403)
    _ensure_open(duel, now)
    if duel.status == "invited":
        raise DuelError("Waiting for your opponent to accept.", 409)
    if slot_attempt_id(duel, user.id):
        raise DuelError("You already have an accepted attempt in this duel.", 409)
    pending = db.scalar(
        select(func.count()).where(Attempt.duel_id == duel.id, Attempt.user_id == user.id, Attempt.status.in_(PENDING_ATTEMPT))
    )
    if pending:
        raise DuelError("Your previous attempt is still being analyzed.", 409)
    used = db.scalar(
        select(func.count()).where(Attempt.duel_id == duel.id, Attempt.user_id == user.id, Attempt.status.in_(UPLOADED_ATTEMPT))
    )
    if used >= settings.max_official_attempts_per_duel:
        raise DuelError("You have used all official attempts for this duel.", 409)


def refresh_status(db: Session, duel: Duel, now: datetime) -> None:
    if duel.status not in ("awaiting_attempts", "analyzing"):
        return
    states = []
    for uid in (duel.challenger_id, duel.opponent_id):
        if slot_attempt_id(duel, uid):
            states.append("done")
            continue
        pending = db.scalar(
            select(func.count()).where(Attempt.duel_id == duel.id, Attempt.user_id == uid, Attempt.status.in_(PENDING_ATTEMPT))
        )
        states.append("pending" if pending else "missing")
    if "missing" in states:
        duel.status = "expired" if now >= duel.expires_at else "awaiting_attempts"
    else:
        duel.status = "analyzing"


def on_attempt_finished(db: Session, attempt: Attempt, now: datetime, settings: Settings) -> bool:
    """Called after analysis; returns True if this call finalized the duel."""
    if attempt.duel_id is None or attempt.mode != "official":
        return False
    duel = db.get(Duel, attempt.duel_id)
    if duel is None or duel.status not in ("awaiting_attempts", "analyzing"):
        return False
    if attempt.status == "scored" and not slot_attempt_id(duel, attempt.user_id):
        if attempt.user_id == duel.challenger_id:
            duel.challenger_attempt_id = attempt.id
        else:
            duel.opponent_attempt_id = attempt.id
        db.flush()
    if duel.challenger_attempt_id and duel.opponent_attempt_id:
        return finalize(db, duel.id, now, settings)
    refresh_status(db, duel, now)
    return False


def finalize(db: Session, duel_id: str, now: datetime, settings: Settings) -> bool:
    """Exactly-once: the conditional UPDATE lets only one caller move the duel to finalized."""
    res = db.execute(
        update(Duel)
        .where(
            Duel.id == duel_id,
            Duel.status.in_(("awaiting_attempts", "analyzing")),
            Duel.challenger_attempt_id.is_not(None),
            Duel.opponent_attempt_id.is_not(None),
        )
        .values(status="finalized", finalized_at=now)
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        return False
    duel = db.get(Duel, duel_id)
    db.refresh(duel)
    a = db.get(Attempt, duel.challenger_attempt_id)
    b = db.get(Attempt, duel.opponent_attempt_id)
    # Unrounded totals; an invalid attempt can never be in a slot, so never wins.
    diff = a.total_score - b.total_score
    if abs(diff) <= settings.draw_margin:
        outcome, duel.is_draw, duel.winner_id = 0.5, True, None
    elif diff > 0:
        outcome, duel.winner_id = 1.0, duel.challenger_id
    else:
        outcome, duel.winner_id = 0.0, duel.opponent_id

    challenger, opponent = db.get(User, duel.challenger_id), db.get(User, duel.opponent_id)
    d_a = rating_delta(challenger.rating, opponent.rating, outcome, settings.elo_k)
    d_b = rating_delta(opponent.rating, challenger.rating, 1.0 - outcome, settings.elo_k)
    challenger.rating += d_a
    opponent.rating += d_b
    duel.challenger_rating_delta, duel.opponent_rating_delta = d_a, d_b
    db.flush()
    return True


def expire_due(db: Session, now: datetime) -> int:
    """Close duels past expiry that lack two accepted attempts. Pending analyses may still finish."""
    count = 0
    for duel in db.scalars(select(Duel).where(Duel.status.in_(OPEN_STATES), Duel.expires_at <= now)):
        if duel.status == "invited":
            duel.status = "expired"
            count += 1
            continue
        refresh_status(db, duel, now)
        count += duel.status == "expired"
    return count
