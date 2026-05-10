"""Phase 6 Plan 03 page-level tests (D-07 / WIZ-01).

Tests cover:
- GET / redirects to /setup when Vibe.count==0 AND Track.count(user_rating>0)>=1.
- GET / does NOT redirect when at least one Vibe row exists.
- GET / does NOT redirect when there are no rated tracks.
- app/main.py registers api_setup router BEFORE pages.router (so /setup pages
  aren't accidentally caught by pages.py).
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel


@pytest.fixture
def client_with_phase6(test_engine) -> Generator[TestClient, None, None]:
    """TestClient with Phase 5 + Phase 6 tables registered."""
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401
    from app.models.vibe import (  # noqa: F401
        ManagedPlaylist,
        SetupState,
        TrackVibe,
        Vibe,
    )

    SQLModel.metadata.create_all(test_engine)

    from app.main import app

    with TestClient(app) as c:
        yield c

    SQLModel.metadata.drop_all(test_engine)


def _seed_plex_configured(session: Session) -> None:
    """Save a non-empty Plex ServiceConfig so home() proceeds past the welcome page."""
    from app.services.settings_service import save_setting

    save_setting(session, "plex", "http://plex.local", "fake-token")


def _seed_rated_track(session: Session, rk: str = "1", rating: float = 8.0) -> None:
    from app.models.track import Track

    session.add(
        Track(
            plex_rating_key=rk,
            title=f"Title {rk}",
            artist="Artist",
            album="Album",
            user_rating=rating,
        )
    )
    session.commit()


def _seed_unrated_track(session: Session, rk: str = "2") -> None:
    from app.models.track import Track

    session.add(
        Track(
            plex_rating_key=rk,
            title=f"Title {rk}",
            artist="Artist",
            album="Album",
            user_rating=None,
        )
    )
    session.commit()


def _seed_vibe(session: Session, name: str = "Existing Vibe") -> None:
    from app.models.vibe import Vibe

    session.add(
        Vibe(
            name=name,
            created_at=datetime.now(timezone.utc).isoformat(),
            is_active=True,
        )
    )
    session.commit()


# ---------------------------------------------------------------------------
# Test 1: D-07 redirect — Vibe.count=0 + rated > 0 → /setup
# ---------------------------------------------------------------------------
def test_get_root_redirects_to_setup_when_no_vibes_and_rated_tracks_exist(
    client_with_phase6, test_engine
):
    """GET / returns 302/307 with Location=/setup (D-07)."""
    with Session(test_engine) as session:
        _seed_plex_configured(session)
        _seed_rated_track(session, rk="1", rating=8.0)

    response = client_with_phase6.get("/", follow_redirects=False)
    assert response.status_code in (302, 307), (
        f"Expected redirect, got {response.status_code}"
    )
    assert response.headers["location"] == "/setup", (
        f"Expected redirect to /setup, got {response.headers.get('location')}"
    )


# ---------------------------------------------------------------------------
# Test 2: At least 1 Vibe row → no redirect
# ---------------------------------------------------------------------------
def test_get_root_does_not_redirect_when_vibes_exist(
    client_with_phase6, test_engine
):
    """When any Vibe row exists, / no longer redirects (returns chat.html)."""
    with Session(test_engine) as session:
        _seed_plex_configured(session)
        _seed_rated_track(session, rk="1", rating=8.0)
        _seed_vibe(session, "Existing Vibe")

    response = client_with_phase6.get("/", follow_redirects=False)
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}; headers={response.headers}"
    )


# ---------------------------------------------------------------------------
# Test 3: No rated tracks → no redirect
# ---------------------------------------------------------------------------
def test_get_root_does_not_redirect_when_no_rated_tracks(
    client_with_phase6, test_engine
):
    """No rated tracks → don't redirect (wizard would gate at <30 anyway)."""
    with Session(test_engine) as session:
        _seed_plex_configured(session)
        _seed_unrated_track(session, rk="2")

    response = client_with_phase6.get("/", follow_redirects=False)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Test 14 (Plan): main.py registers api_setup BEFORE pages.router
# ---------------------------------------------------------------------------
def test_api_setup_router_registered_before_pages_router():
    """api_setup must include_router BEFORE pages.router so /setup/* routes win."""
    main_path = Path(__file__).parent.parent / "app" / "main.py"
    src = main_path.read_text()
    tree = ast.parse(src)

    include_call_router_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "include_router"
                and node.args
            ):
                arg = node.args[0]
                # arg is e.g. api_setup.router or pages.router
                if isinstance(arg, ast.Attribute) and isinstance(
                    arg.value, ast.Name
                ):
                    include_call_router_names.append(arg.value.id)
                elif isinstance(arg, ast.Name):
                    include_call_router_names.append(arg.id)

    assert "api_setup" in include_call_router_names, (
        f"api_setup not registered in app/main.py. include_router calls: "
        f"{include_call_router_names}"
    )
    assert "pages" in include_call_router_names, (
        f"pages router not in app/main.py. include_router calls: "
        f"{include_call_router_names}"
    )

    setup_idx = include_call_router_names.index("api_setup")
    pages_idx = include_call_router_names.index("pages")
    assert setup_idx < pages_idx, (
        f"api_setup must be registered BEFORE pages.router; got "
        f"setup_idx={setup_idx}, pages_idx={pages_idx}"
    )
