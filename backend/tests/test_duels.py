"""Duel integrity: seeded wins, draws, expiry, retries and duplicate callbacks."""

import pytest

from app import duels
from app.models import User


def test_elo_formula():
    assert duels.expected_score(1000, 1000) == pytest.approx(0.5)
    assert duels.rating_delta(1000, 1000, 1.0, 24) == pytest.approx(12.0)
    assert duels.rating_delta(1000, 1000, 0.5, 24) == pytest.approx(0.0)
    # An upset moves ratings more than an expected win.
    assert duels.rating_delta(900, 1100, 1.0, 24) > duels.rating_delta(1100, 900, 1.0, 24)


def _ratings(app):
    with app.state.database.session() as db:
        return {u.username: u.rating for u in db.query(User)}


def test_full_duel_win_and_ratings(api, client, app):
    a, b = api.user("alice"), api.user("bob")
    duel_id = api.duel(a, b, "bob")

    first = api.attempt(a, "official", duel_id, {"noise_px": 1})
    assert first.json()["status"] == "scored"
    assert first.json()["result_hidden"] is True and first.json()["result"] is None

    d = client.get(f"/api/duels/{duel_id}", headers=b).json()
    assert d["opponent_progress"] == "done" and d["outcome"] is None

    api.attempt(b, "official", duel_id, {"noise_px": 3, "delay_s": 0.35})
    d = client.get(f"/api/duels/{duel_id}", headers=a).json()
    assert d["status"] == "finalized"
    assert d["outcome"]["winner"] == "alice"
    assert d["outcome"]["challenger"]["rating_delta"] == pytest.approx(12.0)

    revealed = client.get(f"/api/attempts/{first.json()['id']}", headers=a).json()
    assert revealed["result"]["total"] > 0 and revealed["result"]["feedback"]
    # The opponent sees the score but not private coaching feedback.
    other = client.get(f"/api/attempts/{first.json()['id']}", headers=b).json()
    assert other["result"]["total"] == revealed["result"]["total"] and "feedback" not in other["result"]

    assert _ratings(app) == {"alice": pytest.approx(1012.0), "bob": pytest.approx(988.0)}


def test_close_scores_are_a_draw(api, client, app):
    a, b = api.user("alice"), api.user("bob")
    duel_id = api.duel(a, b, "bob")
    api.attempt(a, "official", duel_id, {"noise_px": 2, "seed": 1})
    api.attempt(b, "official", duel_id, {"noise_px": 2, "seed": 2})
    d = client.get(f"/api/duels/{duel_id}", headers=a).json()
    assert d["outcome"]["is_draw"] and d["outcome"]["winner"] is None
    assert _ratings(app) == {"alice": pytest.approx(1000.0), "bob": pytest.approx(1000.0)}


def test_invalid_attempt_allows_retry_and_never_wins(api, client):
    a, b = api.user("alice"), api.user("bob")
    duel_id = api.duel(a, b, "bob")
    bad = api.attempt(a, "official", duel_id, {"occlude": [3, 7]}).json()
    assert bad["status"] == "invalid" and "visible" in bad["invalid_detail"]
    d = client.get(f"/api/duels/{duel_id}", headers=a).json()
    assert d["status"] == "awaiting_attempts" and d["attempts_left"] == 2

    assert api.attempt(a, "official", duel_id, {}).json()["status"] == "scored"
    again = api.attempt(a, "official", duel_id, {})
    assert again.status_code == 409  # one accepted official attempt per player


def test_attempt_cap(api):
    a, b = api.user("alice"), api.user("bob")
    duel_id = api.duel(a, b, "bob")
    for _ in range(3):
        assert api.attempt(a, "official", duel_id, {"occlude": [0, 10]}).json()["status"] == "invalid"
    r = api.attempt(a, "official", duel_id, {})
    assert r.status_code == 409 and "all official attempts" in r.json()["detail"]


def test_duplicate_finalize_is_noop(api, app, clock):
    a, b = api.user("alice"), api.user("bob")
    duel_id = api.duel(a, b, "bob")
    api.attempt(a, "official", duel_id, {"noise_px": 1})
    api.attempt(b, "official", duel_id, {"delay_s": 0.4})
    before = _ratings(app)
    with app.state.database.session() as db:
        assert duels.finalize(db, duel_id, clock(), app.state.settings) is False
        db.commit()
    assert _ratings(app) == before


def test_duplicate_upload_returns_existing_submission(api, client):
    h = api.user("alice")
    r = client.post("/api/attempts", headers=h, json={"challenge_version_id": api.version_id(h), "mode": "practice"})
    aid = r.json()["id"]
    first = api.upload(h, aid, {})
    second = api.upload(h, aid, {"delay_s": 1.0})
    assert first.status_code == second.status_code == 202
    assert second.json()["status"] == "queued"
    api.run(aid)
    assert client.get(f"/api/attempts/{aid}", headers=h).json()["result"]["total"] > 99  # first upload was kept


def test_expired_duel_has_no_rating_change(api, client, app, clock):
    a, b = api.user("alice"), api.user("bob")
    duel_id = api.duel(a, b, "bob")
    api.attempt(a, "official", duel_id, {})
    clock.advance(hours=49)
    d = client.get(f"/api/duels/{duel_id}", headers=a).json()
    assert d["status"] == "expired"
    assert api.attempt(b, "official", duel_id, {}).status_code == 409
    assert _ratings(app) == {"alice": 1000.0, "bob": 1000.0}


def test_upload_in_time_is_analyzed_after_expiry(api, client, clock):
    a, b = api.user("alice"), api.user("bob")
    duel_id = api.duel(a, b, "bob")
    api.attempt(a, "official", duel_id, {})
    pending = api.attempt(b, "official", duel_id, {}, run=False).json()
    assert client.get(f"/api/duels/{duel_id}", headers=a).json()["status"] == "analyzing"
    clock.advance(hours=49)
    assert client.get(f"/api/duels/{duel_id}", headers=a).json()["status"] == "analyzing"
    api.run(pending["id"])
    assert client.get(f"/api/duels/{duel_id}", headers=a).json()["status"] == "finalized"


def test_technical_failure_retries_then_fails(api, client):
    h = api.user("alice")
    r = api.attempt(h, "practice", recipe={"crash": True}, run=False).json()
    statuses = [api.run(r["id"]) for _ in range(3)]
    assert statuses == ["retry", "retry", "technical_failure"]


def test_declined_and_self_duels(api, client):
    a, b = api.user("alice"), api.user("bob")
    assert client.post("/api/duels", headers=a, json={"opponent_username": "alice", "challenge_slug": "demo"}).status_code == 400
    duel_id = client.post("/api/duels", headers=a, json={"opponent_username": "bob", "challenge_slug": "demo"}).json()["id"]
    assert client.post(f"/api/duels/{duel_id}/accept", headers=a).status_code == 403
    assert client.post(f"/api/duels/{duel_id}/decline", headers=b).json()["status"] == "declined"
    assert api.attempt(a, "official", duel_id, {}).status_code == 409


def test_practice_never_touches_records(api, client, app):
    h = api.user("alice")
    r = api.attempt(h, "practice", recipe={"noise_px": 2}).json()
    assert r["result"]["total"] > 90 and r["result"]["feedback"]
    rec = client.get("/api/users/alice/record", headers=h).json()
    assert rec["battles"] == 0 and rec["win_rate"] is None
    assert _ratings(app)["alice"] == 1000.0


def test_raw_video_deleted_after_analysis(api, app):
    h = api.user("alice")
    r = api.attempt(h, "practice", recipe={})
    assert r.json()["status"] == "scored"
    assert list(app.state.settings.uploads_dir.iterdir()) == []
