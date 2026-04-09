"""Background analysis orchestrator with state machine for audio feature extraction.

Provides start/stop/status control over batch analysis of un-analyzed tracks.
Mirrors the sync_service.py pattern with pause/resume and ETA tracking.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlmodel import Session, select

from app.database import get_engine
from app.models.track import Track
from app.services.audio_analyzer import extract_features, remap_plex_path

logger = logging.getLogger(__name__)

# T-03-05: Max file size for analysis (100 MB)
MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024


class AnalysisStateEnum(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class AnalysisStatus:
    state: AnalysisStateEnum = AnalysisStateEnum.IDLE
    total_tracks: int = 0
    analyzed_tracks: int = 0
    failed_tracks: int = 0
    current_track: str = ""
    avg_seconds_per_track: float = 0.0
    errors: list = field(default_factory=list)
    _times: list = field(default_factory=list)

    @property
    def eta_display(self) -> str:
        """Calculate and format ETA string based on remaining tracks and avg time."""
        remaining = self.total_tracks - self.analyzed_tracks - self.failed_tracks
        if remaining <= 0 or self.avg_seconds_per_track <= 0:
            return ""
        total_seconds = int(remaining * self.avg_seconds_per_track)
        if total_seconds >= 3600:
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            return f"~{hours}h {minutes}m remaining"
        elif total_seconds >= 60:
            minutes = total_seconds // 60
            return f"~{minutes}m remaining"
        else:
            return f"~{total_seconds}s remaining"


_analysis_status = AnalysisStatus()


def get_analysis_status() -> AnalysisStatus:
    """Return the current in-memory analysis status."""
    return _analysis_status


def _detect_plex_music_root_sync() -> str:
    """Auto-detect Plex music root from common prefix of file paths.

    Queries first 10 tracks with file_path not null, finds longest common path prefix.
    Falls back to settings plex_music_root or "/" if insufficient data.
    """
    # Strategy: check settings for explicit plex_music_root first,
    # then sample DIVERSE tracks (different artists) for reliable auto-detection.
    engine = get_engine()
    with Session(engine) as session:
        # Priority 1: User-configured root in settings
        try:
            from app.services.settings_service import get_setting
            setting = get_setting(session, "plex")
            if setting and setting.extra_config:
                root = setting.extra_config.get("plex_music_root")
                if root:
                    logger.info("Using configured Plex music root: %s", root)
                    return root
        except Exception:
            pass

        # Priority 2: Auto-detect from diverse sample of tracks (different artists)
        # Use DISTINCT artists to ensure we sample broadly, not from one album
        from sqlalchemy import distinct, func as sa_func
        statement = (
            select(Track)
            .where(Track.file_path.isnot(None))  # type: ignore[union-attr]
            .group_by(Track.artist)
            .limit(50)
        )
        tracks = session.exec(statement).all()

        if len(tracks) >= 2:
            paths = [t.file_path for t in tracks if t.file_path]
            if len(paths) >= 2:
                try:
                    common = os.path.commonpath(paths)
                    logger.info("Auto-detected Plex music root: %s (from %d diverse artists, e.g. %s)", common, len(paths), paths[0])
                    return common
                except ValueError:
                    pass

    logger.warning("Could not detect Plex music root — falling back to /")
    return "/"


def _analyze_single_track_sync(track_id: int, plex_music_root: str) -> dict:
    """Analyze a single track synchronously. Returns result dict.

    Result dict has keys: success (bool), features (dict|None), error (str|None), elapsed (float).
    """
    engine = get_engine()
    with Session(engine) as session:
        track = session.get(Track, track_id)
        if not track or not track.file_path:
            return {"success": False, "error": "Track not found or no file path", "features": None, "elapsed": 0.0}

        start = time.monotonic()

        try:
            remapped_path = remap_plex_path(track.file_path, plex_music_root)

            # Check file exists
            if not os.path.isfile(remapped_path):
                error_msg = f"File not found: {remapped_path} (plex_path={track.file_path}, root={plex_music_root})"
                logger.warning(error_msg)
                track.analysis_error = error_msg
                session.add(track)
                session.commit()
                elapsed = time.monotonic() - start
                return {"success": False, "error": error_msg, "features": None, "elapsed": elapsed}

            # T-03-05: Check file size
            file_size = os.path.getsize(remapped_path)
            if file_size > MAX_FILE_SIZE_BYTES:
                track.analysis_error = f"File too large ({file_size} bytes): {track.title}"
                session.add(track)
                session.commit()
                elapsed = time.monotonic() - start
                return {"success": False, "error": f"File too large: {track.title}", "features": None, "elapsed": elapsed}

            # Extract features (CPU-bound)
            features = extract_features(remapped_path)

            # Update track with features
            now = datetime.now(timezone.utc).isoformat()
            track.energy = features["energy"]
            track.tempo = features["tempo"]
            track.danceability = features["danceability"]
            track.valence = features["valence"]
            track.musical_key = features["musical_key"]
            track.scale = features["scale"]
            track.spectral_complexity = features["spectral_complexity"]
            track.loudness = features["loudness"]
            track.analyzed_at = now
            track.analysis_error = None
            session.add(track)
            session.commit()

            elapsed = time.monotonic() - start
            return {"success": True, "features": features, "error": None, "elapsed": elapsed}

        except Exception as exc:
            track.analysis_error = str(exc)
            session.add(track)
            session.commit()
            elapsed = time.monotonic() - start
            return {"success": False, "error": str(exc), "features": None, "elapsed": elapsed}


async def run_analysis() -> None:
    """Main analysis entry point. Processes un-analyzed tracks with pause/resume support.

    Prevents concurrent execution by checking _analysis_status.state.
    Updates in-memory AnalysisStatus for UI progress tracking.
    """
    global _analysis_status

    # Guard: prevent concurrent execution (T-03-04)
    if _analysis_status.state == AnalysisStateEnum.RUNNING:
        return

    _analysis_status = AnalysisStatus(state=AnalysisStateEnum.RUNNING)

    try:
        # Detect plex music root
        plex_music_root = await asyncio.to_thread(_detect_plex_music_root_sync)

        # Query un-analyzed tracks
        engine = get_engine()

        def _get_unanalyzed_track_ids() -> list:
            with Session(engine) as session:
                statement = select(Track.id, Track.artist, Track.title).where(
                    Track.analyzed_at.is_(None),  # type: ignore[union-attr]
                    Track.file_path.isnot(None),  # type: ignore[union-attr]
                )
                results = session.exec(statement).all()
                return [(r[0], r[1], r[2]) for r in results]

        track_info = await asyncio.to_thread(_get_unanalyzed_track_ids)
        _analysis_status.total_tracks = len(track_info)

        for track_id, artist, title in track_info:
            # Check if paused (D-03)
            if _analysis_status.state == AnalysisStateEnum.PAUSED:
                break

            _analysis_status.current_track = f"{artist} - {title}"

            # Run analysis in thread (CPU-bound, must not block event loop)
            result = await asyncio.to_thread(
                _analyze_single_track_sync, track_id, plex_music_root
            )

            if result["success"]:
                _analysis_status.analyzed_tracks += 1
            else:
                _analysis_status.failed_tracks += 1
                # T-03-06: Only track title + generic error (no full paths in API)
                error_entry = {
                    "track": f"{artist} - {title}",
                    "error": result["error"] or "Unknown error",
                }
                if len(_analysis_status.errors) < 50:
                    _analysis_status.errors.append(error_entry)

            # Update rolling average (window of 50)
            if result["elapsed"] > 0:
                _analysis_status._times.append(result["elapsed"])
                if len(_analysis_status._times) > 50:
                    _analysis_status._times = _analysis_status._times[-50:]
                _analysis_status.avg_seconds_per_track = (
                    sum(_analysis_status._times) / len(_analysis_status._times)
                )

        # Set final state
        if _analysis_status.state != AnalysisStateEnum.PAUSED:
            _analysis_status.state = AnalysisStateEnum.COMPLETED
        _analysis_status.current_track = ""

    except Exception as exc:
        _analysis_status.state = AnalysisStateEnum.FAILED
        _analysis_status.current_track = ""
        logger.exception("Analysis failed: %s", exc)


async def stop_analysis() -> None:
    """Pause the analysis. The run loop checks this flag each iteration and breaks."""
    global _analysis_status
    _analysis_status.state = AnalysisStateEnum.PAUSED


async def trigger_post_sync_analysis() -> None:
    """Auto-trigger analysis after sync completes (D-01).

    Only triggers if not already RUNNING or PAUSED, and un-analyzed tracks exist.
    """
    global _analysis_status

    if _analysis_status.state in (AnalysisStateEnum.RUNNING, AnalysisStateEnum.PAUSED):
        return

    # Check if there are un-analyzed tracks
    engine = get_engine()

    def _has_unanalyzed() -> bool:
        with Session(engine) as session:
            statement = select(Track.id).where(
                Track.analyzed_at.is_(None),  # type: ignore[union-attr]
                Track.file_path.isnot(None),  # type: ignore[union-attr]
            ).limit(1)
            result = session.exec(statement).first()
            return result is not None

    has_tracks = await asyncio.to_thread(_has_unanalyzed)
    if has_tracks:
        asyncio.create_task(run_analysis())
