"""Tests for app/services/taste_profile_service.py — full taste profile (D-17, D-18, RATE-05).

Verifies:
- recompute populates rated_track_count + 4-D centroid + top artists/genres + summary_text + computed_at.
- recompute is a no-op when there are no rated tracks (no Anthropic call).
- maybe_recompute_after_rating_change triggers recompute on >=10% rated-set delta accumulated
  across multiple events (boundary off-by-one test that catches static-state mistakes).
- Static AST scan: NO scikit-learn import in this Phase 5 file (clustering = Phase 6 only).
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, select


@pytest.fixture
def db_with_phase5(test_engine):
    """Create all Phase 5 tables and yield the engine."""
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401

    SQLModel.metadata.create_all(test_engine)
    yield test_engine
    SQLModel.metadata.drop_all(test_engine)


@pytest.mark.asyncio
class TestRecompute:
    """Tests for recompute() — full taste profile build (RATE-05)."""

    @patch("app.services.taste_profile_service.get_anthropic_client_v2")
    async def test_recompute(self, mock_factory, db_with_phase5):
        """4 rated tracks produce centroid + top artists/genres + summary_text from mocked LLM."""
        from app.models.taste_profile import TasteProfile
        from app.models.track import Track
        from app.services.taste_profile_service import (
            TasteProfileSummary,
            recompute,
        )

        # Pre-populate 4 rated tracks with explicit audio features.
        with Session(db_with_phase5) as session:
            session.add(
                Track(
                    plex_rating_key="t1",
                    title="Track 1",
                    artist="Aphex Twin",
                    album="SAW2",
                    genre="electronic, ambient",
                    user_rating=9.0,
                    energy=0.6,
                    tempo=120.0,
                    danceability=0.5,
                    valence=0.4,
                )
            )
            session.add(
                Track(
                    plex_rating_key="t2",
                    title="Track 2",
                    artist="Aphex Twin",
                    album="Drukqs",
                    genre="electronic, idm",
                    user_rating=8.0,
                    energy=0.7,
                    tempo=130.0,
                    danceability=0.6,
                    valence=0.5,
                )
            )
            session.add(
                Track(
                    plex_rating_key="t3",
                    title="Track 3",
                    artist="Boards of Canada",
                    album="MHTRTC",
                    genre="electronic, downtempo",
                    user_rating=10.0,
                    energy=0.5,
                    tempo=110.0,
                    danceability=0.4,
                    valence=0.6,
                )
            )
            session.add(
                Track(
                    plex_rating_key="t4",
                    title="Track 4",
                    artist="Boards of Canada",
                    album="Geogaddi",
                    genre="electronic, downtempo",
                    user_rating=9.0,
                    energy=0.6,
                    tempo=115.0,
                    danceability=0.45,
                    valence=0.55,
                )
            )
            session.commit()

        # Mock the AnthropicClient factory + the call that returns a summary.
        mock_client = MagicMock()
        mock_client.call_with_structured_output = AsyncMock(
            return_value=TasteProfileSummary(
                summary_text="Your taste leans toward upbeat electronic with downtempo accents."
            )
        )
        mock_factory.return_value = mock_client

        await recompute(triggered_by="manual")

        with Session(db_with_phase5) as session:
            rows = session.exec(select(TasteProfile)).all()
            assert len(rows) == 1
            row = rows[0]
            assert row.id == 1
            assert row.rated_track_count == 4
            # Centroid is the mean of 4 values: (0.6+0.7+0.5+0.6)/4 = 0.6
            assert abs(row.centroid_energy - 0.6) < 0.01
            # Tempo: (120+130+110+115)/4 = 118.75
            assert abs(row.centroid_tempo - 118.75) < 0.1
            # Danceability: (0.5+0.6+0.4+0.45)/4 = 0.4875
            assert abs(row.centroid_danceability - 0.4875) < 0.01
            # Valence: (0.4+0.5+0.6+0.55)/4 = 0.5125
            assert abs(row.centroid_valence - 0.5125) < 0.01

            top_artists = json.loads(row.top_artists_json)
            artist_names = {a["artist"] for a in top_artists}
            assert "Aphex Twin" in artist_names
            assert "Boards of Canada" in artist_names

            top_genres = json.loads(row.top_genres_json)
            genre_names = {g["genre"] for g in top_genres}
            assert "electronic" in genre_names

            assert "upbeat electronic" in row.summary_text
            assert row.computed_at is not None
            datetime.fromisoformat(row.computed_at)

        # Anthropic was called exactly once.
        mock_client.call_with_structured_output.assert_awaited_once()

    @patch("app.services.taste_profile_service.get_anthropic_client_v2")
    async def test_recompute_no_rated_tracks(self, mock_factory, db_with_phase5):
        """Empty rated set → return early; no Anthropic call; row stays default."""
        from app.models.taste_profile import TasteProfile
        from app.services.taste_profile_service import recompute

        mock_factory.return_value = MagicMock()

        await recompute(triggered_by="manual")

        with Session(db_with_phase5) as session:
            rows = session.exec(select(TasteProfile)).all()
            # Either no row created, or a default row with rated_track_count=0
            if rows:
                assert rows[0].rated_track_count == 0
                assert rows[0].summary_text == ""

        # Anthropic was NEVER called.
        mock_factory.assert_not_called()


@pytest.mark.asyncio
class TestMaybeRecomputeAfterRatingChange:
    """Tests for D-18 trigger — accumulated >=10% rated-set delta."""

    @patch("app.services.taste_profile_service.recompute", new_callable=AsyncMock)
    async def test_maybe_recompute_after_rating_change_below_threshold(
        self, mock_recompute, db_with_phase5
    ):
        """9 insertions over 100 baseline → delta 9% < 10% → recompute NOT called."""
        from app.models.taste_profile import TasteProfile
        from app.models.track import Track
        from app.services.taste_profile_service import (
            maybe_recompute_after_rating_change,
        )

        # Setup: TasteProfile.rated_track_count=100 + 100 already-rated tracks.
        with Session(db_with_phase5) as session:
            session.add(
                TasteProfile(
                    id=1,
                    rated_track_count=100,
                    computed_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            for i in range(100):
                session.add(
                    Track(
                        plex_rating_key=f"seed-{i}",
                        title=f"t{i}",
                        artist="a",
                        album="b",
                        user_rating=8.0,
                    )
                )
            session.commit()

        # 9 RatingChanged events — each inserts ONE rated track THEN calls the hook.
        for i in range(9):
            with Session(db_with_phase5) as session:
                session.add(
                    Track(
                        plex_rating_key=f"new-{i}",
                        title=f"x{i}",
                        artist="a",
                        album="b",
                        user_rating=8.0,
                    )
                )
                session.commit()
            await maybe_recompute_after_rating_change()

        # delta = 9/100 = 9% < 10% → recompute never called.
        assert mock_recompute.call_count == 0

    @patch("app.services.taste_profile_service.recompute", new_callable=AsyncMock)
    async def test_maybe_recompute_after_rating_change_at_threshold(
        self, mock_recompute, db_with_phase5
    ):
        """10 insertions over 100 baseline → delta 10% == threshold → recompute called exactly once.

        Catches the off-by-one boundary: a static "DB has 110 rows" setup wouldn't expose it.
        """
        from app.models.taste_profile import TasteProfile
        from app.models.track import Track
        from app.services.taste_profile_service import (
            maybe_recompute_after_rating_change,
        )

        with Session(db_with_phase5) as session:
            session.add(
                TasteProfile(
                    id=1,
                    rated_track_count=100,
                    computed_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            for i in range(100):
                session.add(
                    Track(
                        plex_rating_key=f"seed-{i}",
                        title=f"t{i}",
                        artist="a",
                        album="b",
                        user_rating=8.0,
                    )
                )
            session.commit()

        # First 9 insertions: delta still 9%.
        for i in range(9):
            with Session(db_with_phase5) as session:
                session.add(
                    Track(
                        plex_rating_key=f"new-{i}",
                        title=f"x{i}",
                        artist="a",
                        album="b",
                        user_rating=8.0,
                    )
                )
                session.commit()
            await maybe_recompute_after_rating_change()
        assert mock_recompute.call_count == 0

        # 10th insertion crosses threshold (10/100 = 10%).
        with Session(db_with_phase5) as session:
            session.add(
                Track(
                    plex_rating_key="new-10",
                    title="x10",
                    artist="a",
                    album="b",
                    user_rating=8.0,
                )
            )
            session.commit()
        await maybe_recompute_after_rating_change()
        assert mock_recompute.call_count == 1


def test_no_sklearn_import():
    """Static AST scan: scikit-learn is forbidden in Phase 5 (clustering = Phase 6 only)."""
    path = Path(__file__).parent.parent / "app" / "services" / "taste_profile_service.py"
    source = path.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "sklearn" not in alias.name, (
                    f"sklearn must not be imported in Phase 5 (found: {alias.name})"
                )
        if isinstance(node, ast.ImportFrom):
            assert node.module is None or "sklearn" not in node.module, (
                f"sklearn must not be imported in Phase 5 (found: {node.module})"
            )
