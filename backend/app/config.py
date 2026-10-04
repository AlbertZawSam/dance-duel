from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DD_DATA_DIR", BACKEND_DIR / "data")))
    database_url: str = os.environ.get("DD_DATABASE_URL", "")
    cors_origins: list[str] = field(
        default_factory=lambda: os.environ.get("DD_CORS_ORIGINS", "http://localhost:5173").split(",")
    )
    max_upload_bytes: int = int(os.environ.get("DD_MAX_UPLOAD_MB", "80")) * 1024 * 1024
    allowed_video_types: tuple[str, ...] = ("video/webm", "video/mp4", "video/quicktime")
    duel_expiry_hours: int = int(os.environ.get("DD_DUEL_EXPIRY_HOURS", "48"))
    max_official_attempts_per_duel: int = 3
    max_analysis_retries: int = 2
    draw_margin: float = 2.0
    elo_k: float = 24.0
    initial_rating: float = 1000.0
    raw_video_retention_hours: int = 24
    pose_retention_days: int = 7
    timezone: str = "Asia/Bangkok"
    start_worker: bool = os.environ.get("DD_START_WORKER", "1") == "1"

    def __post_init__(self) -> None:
        if not self.database_url:
            self.database_url = f"sqlite:///{self.data_dir / 'danceduel.db'}"

    @property
    def protected_dir(self) -> Path:
        """Hidden references and songs; never served from a public static route."""
        return self.data_dir / "protected"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def poses_dir(self) -> Path:
        return self.data_dir / "poses"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.protected_dir, self.uploads_dir, self.poses_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
