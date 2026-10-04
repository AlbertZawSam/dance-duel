# Dance Duel — AI-Powered Blind Dance Challenge

CSC493 Intelligent Systems and Applications (2026 theme: AI and Entertainment).

Players learn a short routine, then perform an **official attempt from memory while only the song plays**. The server compares the recording with a **hidden reference** using pretrained pose estimation (RTMPose) and an explainable score. Two players can duel **asynchronously** against the same locked challenge version.

> Scores are *choreography-match estimates* under declared conditions (one person, front-facing camera, full body in frame, indoor light). They are not judgments of artistry, fitness or worth.

## Quick start

```bash
# Backend (Python 3.11+)
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-pose.txt -r requirements-dev.txt   # pose deps download RTMPose weights on first use
python -m scripts.seed_demo --reset                             # synthetic demo content, users and duels
uvicorn app.main:app --port 8000

# Frontend (Node 20+)
cd frontend
npm install
npm run dev          # http://localhost:5173 (proxies /api to :8000)
```

Demo users: `alice`, `bob`, `carol`, `dave`, `erin`. Password: `dance-duel-demo`. The seeded songs are synthesized tones, the references are procedural stick figures, and the seeded duel results come from synthetic players. Every number you see on the demo dashboards is therefore **illustrative**.

Run the tests with `cd backend && pytest`.

## Publishing a real challenge

Use only **original or clearly permitted** songs and reference videos, and record the permission scope.

```bash
cd backend
python -m scripts.prepare_challenge --slug arm-wave --title "Arm Wave" \
  --video refs/arm_wave.mp4 --song refs/arm_wave.mp3 \
  --events "2.0:arm raise,5.5:turn,9.0:freeze" --signature 4-6 \
  --license-note "Original routine and music, recorded for CSC493"
```

- The song file must already be trimmed to the challenge segment. Use `--video-offset` if the dance starts later in the video than the song does.
- Without `--events`, the script suggests movement accents automatically. Use `--dry-run` to review them first.
- Publishing again under the same slug creates a **new immutable version**. Open duels stay locked to the version they started with.
- Publishing fails if the reference does not pass the visibility gate.

## Architecture

| Layer | Responsibility |
|---|---|
| Reference preparation (`scripts/prepare_challenge.py`, `app/publish.py`) | Extract reference keypoints, set events and signature windows, publish an immutable `ChallengeVersion` with hashes, thresholds, weights and licence note |
| React client (`frontend/`) | Camera permission, camera check, audio-clock timing, `MediaRecorder` capture, upload with retry, results and dashboards |
| FastAPI (`app/api.py`) | Auth, attempt tokens, upload validation, duel state machine, result reveal rules, dashboards |
| Analysis worker (`app/worker.py`) | Queued post-attempt analysis: decode → RTMPose → scoring. Bounded retries, then deletion of the raw video |
| SQLite + protected files (`backend/data/`) | Users, versions, attempts, duels. Hidden references and songs live outside any public route |

**Where inference runs:** on the server, after upload. There is no real-time scoring claim. The client never sends a score, and every competitive score is computed on the server.

**Timing:** the song is scheduled on the Web Audio clock. The client measures when the song started relative to the recording, including the browser's reported output latency, and sends that offset. The server samples the recording on the song timeline at 15 Hz. Camera capture latency is **not** compensated yet (see limitations).

## Scoring (`app/scoring/`)

```
Final = 0.50 Pose + 0.25 Timing + 0.15 Dynamics + 0.10 Flow
component score = 100 × max(0, 1 − error / scale)
```

| Component | Measurement | Starting scale |
|---|---|---|
| Pose | Joint angles (8) and limb directions (10), compared per sample with a fixed ±1-sample (≈±67 ms) tolerance. The tolerance shift is chosen per sample, never per feature | 60° |
| Timing | Each signature event is matched within ±0.6 s. The penalty is the absolute offset; a match worse than 45° counts as missed | 0.5 s |
| Dynamics | Joint speed (torso lengths/s, smoothed) compared with the reference | 3.0 |
| Flow | Velocity direction when moving. Both still means no penalty; only one moving means a mismatch | 90° |

- **Normalization:** joint angles and limb directions don't depend on position or body size. Speeds are divided by a robust torso length. Poses are **not** rotated to fit the reference, because that would hide a genuine lean.
- **Visibility gate:** at least 90% of samples must show the full body (all 12 limb joints with confidence ≥ 0.3), and every 2-second window needs at least 80% feature coverage. Otherwise the attempt is **invalid with a retry reason**, never given a low score. Gaps of 2 samples or fewer are interpolated; longer gaps are not.
- **Mirroring:** each challenge has one fixed policy (`none` or `mirror_player`). Left/right is never chosen per frame to maximize a score.
- **Signature windows:** these get 1.5× weight, normalized inside each component, so the total stays within 0–100.
- **No unrestricted time warping:** a consistently late dancer cannot be made to look on time.
- **Feedback:** fixed templates fed by measured features, with no LLM. You get up to two timestamped improvement notes plus one strength, and only well-covered observations qualify.

Every threshold lives in `ScoringConfig` and is stored with each published version.

## Duels, rating, dashboards

- **States:** `invited → awaiting_attempts → analyzing → finalized`, with `declined` and `expired` as terminal states. Duels expire after 48 h by default.
- **Attempts:** each player gets one accepted official attempt, with at most 3 uploads per duel. An invalid recording allows a retry and never counts as a loss. Re-uploading to the same attempt ID is idempotent.
- **Reveal:** no score or feedback is shown to either player until both valid attempts are finished.
- **Finalization:** exactly once, through a conditional `UPDATE`, so duplicate analysis callbacks change nothing.
- **Outcome:** decided on unrounded totals. A difference of 2 points or less is a draw.
- **Dance Rating (experimental):** Elo starting at 1000 with K = 24, `E = 1 / (1 + 10^((Rb − Ra)/400))`. Practice, invalid, expired and declined duels never affect it.
- **Weekly window:** Monday 00:00 in Asia/Bangkok.
  - **Top 3 trending:** finished duels per challenge. Ties are broken by unique players, then challenge ID.
  - **Best 3 users:** ranked by rating among players with 3 or more finished duels that week. At most 2 duels per opponent pair count toward that minimum.
- **Individual record:** battles = wins + losses + draws. Win rate shows N/A when there are no battles.

## Privacy defaults

- Before capture, the client explains that the recording is uploaded to the server.
- Raw video is deleted as soon as analysis ends, success or failure. A cleanup sweep removes anything older than 24 h.
- Derived pose data is kept for 7 days, then deleted. Score records are kept.
- Pose data still counts as personal data (Thailand PDPA). Use access control and HTTPS for any deployment beyond localhost.

## Measured so far (Apple M2, CPU)

| Configuration | 30 s clip, 15 Hz sampling |
|---|---|
| RTMPose `balanced`, detector on every frame | ≈ 190 s |
| RTMPose `lightweight`, detector every 5th sample (**default**) | ≈ 16 s pose time; ≈ 29 s end-to-end from a 1080p, 91 s source file |

Configure with `DD_POSE_MODE` and `DD_DET_FREQUENCY`. The CoreML execution provider failed on these models, so CPU is used. These are benchmarks of a non-dance test clip, **not** evaluation results.

## Known limitations and next steps (aligned with the proposal schedule)

1. **Calibration (Week 7).** The scales are starting guesses. On synthetic data, an entirely different routine still scores about 60, so the scales are too lenient. Calibrate them on development recordings, then freeze before the held-out tests.
2. **Ablations.** Compare Pose-only vs Pose + Timing vs all four components, and raw coordinates vs normalized features, against human ratings.
3. **Event matching under low amplitude.** A performer who moves much less than the reference can produce false "early/late" timing notes. Hand-annotated events and calibration should reduce this.
4. **Camera latency.** Typical browser capture latency is 50–200 ms and is not yet measured. Add a calibration step (for example a clap test) or a per-device constant.
5. **Integrity.** This is a casual competition, not a cheat-proof tournament. A modified client could upload a different video, and nothing stops a player from watching the routine on a second device.
6. **2D only.** Fast spins, floor work, loose clothing and out-of-plane motion degrade accuracy. Start with upright, front-facing routines. COCO-17 has no hand keypoints.
7. **Hosting.** Remote duels between different places need a publicly reachable server, which the 0 THB local cost does not cover.
