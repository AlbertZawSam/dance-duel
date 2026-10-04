from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.factory import create_app  # noqa: E402
from app.publish import publish_version  # noqa: E402
from app.scoring import ChallengeSpec  # noqa: E402
from app.scoring.synthetic import SyntheticRoutine, occlude  # noqa: E402
from app.worker import analyze_attempt  # noqa: E402

ROUTINE = SyntheticRoutine(seed=3, duration_s=12.0)


class FakeExtractor:
    """Stands in for RTMPose: the uploaded 'video' is a JSON recipe for a synthetic dancer."""

    model_id = "fake"

    def extract(self, video_path, song_offset_s, duration_s, fps, recording_duration_s=None):
        recipe = json.loads(Path(video_path).read_text())
        if recipe.get("crash"):
            raise RuntimeError("model exploded")
        occ = recipe.pop("occlude", None)
        seq = ROUTINE.sample(**recipe)
        return occlude(seq, [9, 10], *occ) if occ else seq

    def check_frame(self, image_bytes):
        return {"people": 1, "full_body": True, "brightness": 120.0, "model": self.model_id}


class Clock:
    def __init__(self):
        # A Wednesday, so the Bangkok week window is unambiguous.
        self.now = datetime(2026, 9, 30, 6, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def app(tmp_path, clock):
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings, extractor_factory=FakeExtractor, clock=clock, start_worker=False)
    song = tmp_path / "song.wav"
    song.write_bytes(b"RIFF....WAVE")
    with app.state.database.session() as db:
        publish_version(
            db,
            settings,
            slug="demo",
            title="Demo",
            song_file=song,
            reference=ROUTINE.sample(),
            spec=ChallengeSpec(events=ROUTINE.events()),
            pose_model="fake",
            license_note="test asset",
        )
        db.commit()
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


class Api:
    """Small helper that also runs the worker synchronously after uploads."""

    def __init__(self, client, app):
        self.client, self.app = client, app

    def user(self, name: str) -> dict:
        r = self.client.post("/api/auth/register", json={"username": name, "password": "password123"})
        assert r.status_code == 201, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}

    def version_id(self, h) -> int:
        return self.client.get("/api/challenges/demo", headers=h).json()["current_version"]["id"]

    def attempt(self, h, mode="practice", duel_id=None, recipe=None, run=True):
        r = self.client.post(
            "/api/attempts", headers=h, json={"challenge_version_id": self.version_id(h), "mode": mode, "duel_id": duel_id}
        )
        if r.status_code != 201:
            return r
        aid = r.json()["id"]
        r = self.upload(h, aid, recipe or {})
        assert r.status_code == 202, r.text
        if run:
            self.run(aid)
        return self.client.get(f"/api/attempts/{aid}", headers=h)

    def upload(self, h, attempt_id, recipe):
        return self.client.put(
            f"/api/attempts/{attempt_id}/recording",
            headers=h,
            files={"video": ("take.webm", json.dumps(recipe).encode(), "video/webm")},
            data={"song_offset_ms": "120", "recording_duration_ms": "13000"},
        )

    def run(self, attempt_id):
        with self.app.state.database.session() as db:
            return analyze_attempt(db, attempt_id, FakeExtractor(), self.app.state.settings, self.app.state.clock())

    def duel(self, h_from, h_to, to_name):
        r = self.client.post("/api/duels", headers=h_from, json={"opponent_username": to_name, "challenge_slug": "demo"})
        assert r.status_code == 201, r.text
        duel_id = r.json()["id"]
        assert self.client.post(f"/api/duels/{duel_id}/accept", headers=h_to).status_code == 200
        return duel_id


@pytest.fixture
def api(client, app):
    return Api(client, app)
