"""Publish a challenge version from a permitted reference video and song segment.

The song file must already be trimmed to the challenge segment, and the reference
video must be in sync with it (use --video-offset if the dance starts later in the
video than the song does).

Example:
    python -m scripts.prepare_challenge --slug arm-wave --title "Arm Wave" \
        --video refs/arm_wave.mp4 --song refs/arm_wave.mp3 \
        --signature 4-6 --license-note "Original routine and music by A. Zaw Sam, class use"

Events default to automatically detected movement accents; review them in the
printed summary and pass --events "2.0:arm raise,5.5:turn" to set them by hand.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.pose import RTMPoseExtractor  # noqa: E402
from app.publish import detect_events, publish_version  # noqa: E402
from app.scoring import ChallengeSpec, ScoringConfig, SignatureEvent  # noqa: E402


def parse_events(text: str) -> tuple[SignatureEvent, ...]:
    out = []
    for item in filter(None, (s.strip() for s in text.split(","))):
        t, _, name = item.partition(":")
        out.append(SignatureEvent(float(t), name.strip() or f"move at {t}s"))
    return tuple(out)


def parse_windows(text: str) -> tuple[tuple[float, float], ...]:
    return tuple(tuple(float(x) for x in w.split("-")) for w in filter(None, (s.strip() for s in text.split(","))))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--slug", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--description", default="")
    p.add_argument("--difficulty", default="easy", choices=["easy", "medium", "hard"])
    p.add_argument("--video", required=True, type=Path, help="permitted reference dance video")
    p.add_argument("--song", required=True, type=Path, help="song segment, already trimmed")
    p.add_argument("--duration", type=float, help="challenge length in seconds (default: video length minus offset)")
    p.add_argument("--video-offset", type=float, default=0.0, help="video time (s) at which the song starts")
    p.add_argument("--events", default="", help='"t:name,t:name" signature events in song seconds')
    p.add_argument("--signature", default="", help='"start-end,..." signature windows (1.5x weight)')
    p.add_argument("--mirror-policy", default="none", choices=["none", "mirror_player"])
    p.add_argument("--license-note", required=True)
    p.add_argument("--no-practice-video", action="store_true", help="do not show the reference (video or skeleton) in practice")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    config = ScoringConfig()
    extractor = RTMPoseExtractor()
    if args.duration is None:
        import cv2

        cap = cv2.VideoCapture(str(args.video))
        frames, fps = cap.get(cv2.CAP_PROP_FRAME_COUNT), cap.get(cv2.CAP_PROP_FPS) or 30
        cap.release()
        args.duration = frames / fps - args.video_offset
    print(f"Extracting reference pose ({args.duration:.1f}s at {config.fps} fps) with {extractor.model_id} ...")
    reference = extractor.extract(args.video, args.video_offset, args.duration, config.fps)

    events = parse_events(args.events) or detect_events(reference, config)
    spec = ChallengeSpec(events=events, signature_windows=parse_windows(args.signature), mirror_policy=args.mirror_policy, config=config)
    print(json.dumps({"events": [e.__dict__ for e in events], "signature_windows": spec.signature_windows}, indent=2))
    if args.dry_run:
        return

    settings.ensure_dirs()
    database = Database(settings.database_url)
    database.create_all()
    with database.session() as db:
        version = publish_version(
            db,
            settings,
            slug=args.slug,
            title=args.title,
            description=args.description,
            difficulty=args.difficulty,
            song_file=args.song,
            reference=reference,
            spec=spec,
            pose_model=extractor.model_id,
            license_note=args.license_note,
            practice_video=None if args.no_practice_video else args.video,
            practice_reference_allowed=not args.no_practice_video,
        )
        db.commit()
        print(f"Published {args.slug} v{version.version} (version id {version.id}).")


if __name__ == "__main__":
    main()
