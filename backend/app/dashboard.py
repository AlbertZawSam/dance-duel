"""Weekly Top 3 trending challenges, weekly Best 3 users and individual records."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import Attempt, Challenge, Duel, User

MIN_WEEKLY_DUELS = 3
MAX_COUNTED_PER_OPPONENT = 2  # limits farming eligibility against one friend


def week_bounds(now: datetime, tz_name: str, offset_weeks: int = 0) -> tuple[datetime, datetime]:
    """Monday 00:00 to next Monday 00:00 in local time, returned as UTC."""
    tz = ZoneInfo(tz_name)
    local = now.astimezone(tz)
    start_local = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    start_local += timedelta(weeks=offset_weeks)
    end_local = start_local + timedelta(weeks=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _finalized_in(db: Session, start: datetime, end: datetime) -> list[Duel]:
    return list(
        db.scalars(select(Duel).where(Duel.status == "finalized", Duel.finalized_at >= start, Duel.finalized_at < end))
    )


def trending_challenges(db: Session, start: datetime, end: datetime, limit: int = 3) -> list[dict]:
    duels = defaultdict(int)
    players = defaultdict(set)
    for d in _finalized_in(db, start, end):
        cid = d.challenge_version.challenge_id
        duels[cid] += 1  # one count per duel
        players[cid].update((d.challenger_id, d.opponent_id))
    ranked = sorted(duels, key=lambda c: (-duels[c], -len(players[c]), c))[:limit]
    out = []
    for cid in ranked:
        ch = db.get(Challenge, cid)
        out.append({"challenge_id": cid, "slug": ch.slug, "title": ch.title, "duels": duels[cid], "players": len(players[cid])})
    return out


def best_users(db: Session, start: datetime, end: datetime, limit: int = 3) -> list[dict]:
    pair_counts: Counter = Counter()
    eligible: Counter = Counter()
    for d in sorted(_finalized_in(db, start, end), key=lambda d: d.finalized_at):
        pair = tuple(sorted((d.challenger_id, d.opponent_id)))
        pair_counts[pair] += 1
        if pair_counts[pair] > MAX_COUNTED_PER_OPPONENT:
            continue
        eligible[d.challenger_id] += 1
        eligible[d.opponent_id] += 1
    users = [db.get(User, uid) for uid, n in eligible.items() if n >= MIN_WEEKLY_DUELS]
    users.sort(key=lambda u: (-u.rating, u.id))
    return [
        {"user_id": u.id, "username": u.username, "rating": round(u.rating, 1), "weekly_duels": eligible[u.id]}
        for u in users[:limit]
    ]


def user_record(db: Session, user: User, history_limit: int = 10) -> dict:
    duels = list(
        db.scalars(
            select(Duel)
            .where(Duel.status == "finalized", or_(Duel.challenger_id == user.id, Duel.opponent_id == user.id))
            .order_by(Duel.finalized_at.desc())
        )
    )
    wins = sum(d.winner_id == user.id for d in duels)
    draws = sum(d.is_draw for d in duels)
    losses = len(duels) - wins - draws
    history, totals, components = [], [], defaultdict(list)
    for d in duels:
        mine_first = d.challenger_id == user.id
        my_att = db.get(Attempt, d.challenger_attempt_id if mine_first else d.opponent_attempt_id)
        their_att = db.get(Attempt, d.opponent_attempt_id if mine_first else d.challenger_attempt_id)
        opp = d.opponent if mine_first else d.challenger
        totals.append(my_att.total_score)
        for key in ("pose_score", "timing_score", "dynamics_score", "flow_score"):
            components[key.removesuffix("_score")].append(getattr(my_att, key))
        if len(history) < history_limit:
            history.append({
                "duel_id": d.id,
                "finalized_at": d.finalized_at.isoformat(),
                "challenge": d.challenge_version.challenge.title,
                "opponent": opp.username,
                "my_score": round(my_att.total_score, 1),
                "opponent_score": round(their_att.total_score, 1),
                "result": "draw" if d.is_draw else ("win" if d.winner_id == user.id else "loss"),
                "rating_delta": round(d.challenger_rating_delta if mine_first else d.opponent_rating_delta, 1),
            })
    battles = wins + losses + draws
    return {
        "username": user.username,
        "rating": round(user.rating, 1),
        "battles": battles,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "win_rate": round(wins / battles, 4) if battles else None,  # shown as N/A when None
        "best_score": round(max(totals), 1) if totals else None,
        "average_score": round(sum(totals) / len(totals), 1) if totals else None,
        "average_components": {k: round(sum(v) / len(v), 1) for k, v in components.items()},
        "history": history,
    }
