from datetime import datetime, timezone

from app.dashboard import week_bounds


def test_week_bounds_use_bangkok_monday():
    # Sunday 20:00 UTC is already Monday 03:00 in Bangkok.
    start, end = week_bounds(datetime(2026, 10, 4, 20, 0, tzinfo=timezone.utc), "Asia/Bangkok")
    assert start == datetime(2026, 10, 4, 17, 0, tzinfo=timezone.utc)
    assert (end - start).days == 7


def _play_duel(api, h1, h2, name2, r1, r2):
    duel_id = api.duel(h1, h2, name2)
    api.attempt(h1, "official", duel_id, r1)
    api.attempt(h2, "official", duel_id, r2)


def test_weekly_dashboard_and_record(api, client):
    h = {n: api.user(n) for n in ("alice", "bob", "carol")}
    good, late = {"noise_px": 1}, {"delay_s": 0.4}
    _play_duel(api, h["alice"], h["bob"], "bob", good, late)
    _play_duel(api, h["alice"], h["carol"], "carol", good, late)
    _play_duel(api, h["bob"], h["alice"], "alice", late, good)
    # Third and fourth alice-bob duels: only two per pair count toward eligibility.
    _play_duel(api, h["alice"], h["bob"], "bob", good, late)

    dash = client.get("/api/dashboard/weekly", headers=h["alice"]).json()
    assert dash["timezone"] == "Asia/Bangkok"
    assert dash["trending_challenges"][0] == {"challenge_id": 1, "slug": "demo", "title": "Demo", "duels": 4, "players": 3}
    best = [u["username"] for u in dash["best_users"]]
    assert best == ["alice"]  # alice: 3 eligible (2 vs bob + 1 vs carol); bob only 2 eligible

    rec = client.get("/api/users/alice/record", headers=h["bob"]).json()
    assert (rec["battles"], rec["wins"], rec["losses"], rec["draws"]) == (4, 4, 0, 0)
    assert rec["win_rate"] == 1.0
    assert rec["history"][0]["result"] == "win" and rec["history"][0]["opponent"] == "bob"
    bob = client.get("/api/users/bob/record", headers=h["bob"]).json()
    assert bob["wins"] + bob["losses"] + bob["draws"] == bob["battles"] == 3


def test_requires_auth(client):
    assert client.get("/api/dashboard/weekly").status_code == 401
    assert client.get("/api/challenges", headers={"Authorization": "Bearer nope"}).status_code == 401
