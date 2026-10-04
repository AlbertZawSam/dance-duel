"""Publishing immutable challenge versions (the offline reference-preparation step)."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings
from .models import Challenge, ChallengeVersion
from .scoring import NORMALIZER_VERSION, ChallengeSpec, PoseSequence, SignatureEvent, validate_reference
from .scoring.normalize import prepare


class PublishError(Exception):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_events(reference: PoseSequence, spec_config, max_events: int = 8, min_gap_s: float = 1.5) -> tuple[SignatureEvent, ...]:
    """Suggest signature events at the strongest movement accents (speed peaks) of the reference.

    These are suggestions for the content administrator; hand-annotated events are preferred.
    """
    prep = prepare(reference, spec_config)
    speed = np.where(prep.velocity_mask, np.linalg.norm(prep.velocity, axis=2), 0.0).mean(axis=1)
    speed = np.convolve(speed, np.ones(3) / 3, mode="same")
    edge = spec_config.samples(spec_config.timing_search_s)
    peaks = [
        i for i in range(max(1, edge), len(speed) - max(1, edge))
        if speed[i] >= speed[i - 1] and speed[i] > speed[i + 1]
    ]  # fmt: skip
    chosen: list[int] = []
    gap = spec_config.samples(min_gap_s)
    for i in sorted(peaks, key=lambda i: -speed[i]):
        if all(abs(i - j) >= gap for j in chosen):
            chosen.append(i)
        if len(chosen) == max_events:
            break
    return tuple(SignatureEvent(round(i / spec_config.fps, 3), f"accent {k + 1}") for k, i in enumerate(sorted(chosen)))


def publish_version(
    db: Session,
    settings: Settings,
    *,
    slug: str,
    title: str,
    song_file: Path,
    reference: PoseSequence,
    spec: ChallengeSpec,
    pose_model: str,
    license_note: str,
    description: str = "",
    difficulty: str = "easy",
    practice_video: Path | None = None,
    practice_reference_allowed: bool = True,
    segment_start_s: float = 0.0,
) -> ChallengeVersion:
    if not license_note.strip():
        raise PublishError("Record the permission scope for the song and reference (license_note).")
    problems = validate_reference(reference, spec)
    if problems:
        raise PublishError("Reference failed validation: " + "; ".join(problems))

    challenge = db.scalar(select(Challenge).where(Challenge.slug == slug))
    if challenge is None:
        challenge = Challenge(slug=slug, title=title, description=description, difficulty=difficulty)
        db.add(challenge)
        db.flush()
    else:
        challenge.title, challenge.description, challenge.difficulty = title, description, difficulty
    version_no = (db.scalar(select(func.max(ChallengeVersion.version)).where(ChallengeVersion.challenge_id == challenge.id)) or 0) + 1

    rel_dir = Path(slug) / f"v{version_no}"
    out_dir = settings.protected_dir / rel_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    song_rel = rel_dir / f"song{song_file.suffix.lower()}"
    shutil.copyfile(song_file, settings.protected_dir / song_rel)
    ref_rel = rel_dir / "reference.npz"
    reference.save(settings.protected_dir / ref_rel)
    practice_rel = None
    if practice_video is not None:
        practice_rel = rel_dir / f"practice{practice_video.suffix.lower()}"
        shutil.copyfile(practice_video, settings.protected_dir / practice_rel)

    mime = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".m4a": "audio/mp4"}.get(song_file.suffix.lower(), "application/octet-stream")
    version = ChallengeVersion(
        challenge_id=challenge.id,
        version=version_no,
        duration_s=reference.duration_s,
        song_path=str(song_rel),
        song_sha256=sha256_file(song_file),
        song_mime=mime,
        segment_start_s=segment_start_s,
        segment_end_s=segment_start_s + reference.duration_s,
        reference_pose_path=str(ref_rel),
        reference_sha256=sha256_file(settings.protected_dir / ref_rel),
        practice_video_path=str(practice_rel) if practice_rel else None,
        practice_reference_allowed=practice_reference_allowed,
        pose_model=pose_model,
        normalizer_version=NORMALIZER_VERSION,
        mirror_policy=spec.mirror_policy,
        spec=spec.to_dict(),
        license_note=license_note,
    )
    db.add(version)
    db.flush()
    challenge.current_version_id = version.id
    db.flush()
    return version
