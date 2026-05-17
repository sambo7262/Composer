"""Phase 8 Plan 01 Task 1 — Lidarr client extension tests (D-E1 / DISC-07).

TDD RED → GREEN sequence:
- test_lidarr_connection returns {quality_profiles, metadata_profiles, root_folders}
- add_artist passes BOTH profile IDs + root_dir per Pitfall 14
- get_recent_history lazy-poll surface
- save_lidarr persists 5 extras keys + back-compat mirror
- connection_status partial renders 3 dropdowns; root only when >1
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.templating import Jinja2Templates


# ============================================================================
# test_lidarr_connection — returns three lists, single to_thread, errors preserved
# ============================================================================


@pytest.mark.asyncio
@patch("app.services.lidarr_client.Lidarr")
async def test_connection_test_returns_three_lists(mock_lidarr_cls):
    """test_lidarr_connection returns quality_profiles + metadata_profiles + root_folders."""
    from app.services.lidarr_client import test_lidarr_connection

    mock_lidarr = MagicMock()
    mock_lidarr.quality_profile.get.return_value = [{"id": 1, "name": "FLAC"}]
    mock_lidarr.metadata.get.return_value = [{"id": 2, "name": "Standard"}]
    mock_lidarr.root_folder.get.return_value = [{"id": 3, "path": "/music"}]
    mock_lidarr_cls.return_value = mock_lidarr

    result = await test_lidarr_connection("http://lidarr", "key")

    assert result["success"] is True
    assert result["quality_profiles"] == [{"id": 1, "name": "FLAC"}]
    assert result["metadata_profiles"] == [{"id": 2, "name": "Standard"}]
    assert result["root_folders"] == [{"id": 3, "path": "/music"}]


@pytest.mark.asyncio
@patch("app.services.lidarr_client.asyncio.to_thread")
@patch("app.services.lidarr_client.Lidarr")
async def test_connection_test_fetches_all_three_in_one_to_thread(
    mock_lidarr_cls, mock_to_thread
):
    """All three Lidarr calls go through a SINGLE asyncio.to_thread invocation
    (D-E1 'halve round-trips' intent). _fetch_lidarr_test_payload is invoked
    exactly once and returns the 3-tuple.
    """
    from app.services.lidarr_client import test_lidarr_connection

    # Make to_thread synchronously return the expected 3-tuple
    async def _fake_to_thread(fn, *args, **kwargs):
        return (
            [{"id": 1, "name": "FLAC"}],
            [{"id": 2, "name": "Standard"}],
            [{"id": 3, "path": "/music"}],
        )

    mock_to_thread.side_effect = _fake_to_thread

    await test_lidarr_connection("http://lidarr", "key")

    # Exactly one to_thread call for the payload fetch (NOT three).
    assert mock_to_thread.call_count == 1


@pytest.mark.asyncio
@patch("app.services.lidarr_client.Lidarr")
async def test_connection_test_error_tier_preserved_auth(mock_lidarr_cls):
    """401 → 'Authentication failed' tier preserved."""
    from app.services.lidarr_client import test_lidarr_connection

    mock_lidarr_cls.side_effect = Exception("401 Unauthorized")

    result = await test_lidarr_connection("http://lidarr", "bad-key")

    assert result["success"] is False
    assert "Authentication failed" in result["error"]


@pytest.mark.asyncio
@patch("app.services.lidarr_client.Lidarr")
async def test_connection_test_error_tier_preserved_timeout(mock_lidarr_cls):
    """timeout → 'Connection timed out' tier preserved."""
    from app.services.lidarr_client import test_lidarr_connection

    mock_lidarr_cls.side_effect = Exception("Connection timed out")

    result = await test_lidarr_connection("http://lidarr", "key")

    assert result["success"] is False
    assert "timed out" in result["error"]


@pytest.mark.asyncio
@patch("app.services.lidarr_client.Lidarr")
async def test_connection_test_error_tier_preserved_refused(mock_lidarr_cls):
    """refused → 'Connection refused' tier preserved."""
    from app.services.lidarr_client import test_lidarr_connection

    mock_lidarr_cls.side_effect = Exception("Connection refused by host")

    result = await test_lidarr_connection("http://lidarr", "key")

    assert result["success"] is False
    assert "Connection refused" in result["error"]


# ============================================================================
# add_artist — Pitfall 14 / D-E1
# ============================================================================


@pytest.mark.asyncio
@patch("app.services.lidarr_client.Lidarr")
async def test_add_artist_passes_both_profile_ids_and_root_dir(mock_lidarr_cls):
    """add_artist invokes lidarr.add_artist with BOTH profile IDs + root_dir +
    monitored=True + artist_monitor='all' + search_for_missing_albums=True.
    """
    from app.services.lidarr_client import add_artist

    mock_lidarr = MagicMock()
    mock_lidarr.artist.lookup.return_value = [
        {"foreignArtistId": "abc-mbid", "artistName": "X"}
    ]
    mock_lidarr.artist.add.return_value = {"id": 99, "artistName": "X"}
    mock_lidarr_cls.return_value = mock_lidarr

    result = await add_artist(
        mb_id="abc-mbid",
        url="http://lidarr",
        api_key="key",
        quality_profile_id=1,
        metadata_profile_id=2,
        root_dir="/music",
    )

    assert result["success"] is True
    assert result["lidarr_artist_id"] == 99

    mock_lidarr.artist.add.assert_called_once()
    kwargs = mock_lidarr.artist.add.call_args.kwargs
    assert kwargs["artist"] == {"foreignArtistId": "abc-mbid", "artistName": "X"}
    assert kwargs["root_dir"] == "/music"
    assert kwargs["quality_profile_id"] == 1
    assert kwargs["metadata_profile_id"] == 2
    assert kwargs["monitored"] is True
    assert kwargs["artist_monitor"] == "all"
    assert kwargs["search_for_missing_albums"] is True


@pytest.mark.asyncio
@patch("app.services.lidarr_client.Lidarr")
async def test_add_artist_drops_when_lookup_empty(mock_lidarr_cls):
    """Empty lookup_artist response → return error, DO NOT invoke add_artist."""
    from app.services.lidarr_client import add_artist

    mock_lidarr = MagicMock()
    mock_lidarr.artist.lookup.return_value = []
    mock_lidarr_cls.return_value = mock_lidarr

    result = await add_artist(
        mb_id="zzz-mbid",
        url="http://lidarr",
        api_key="key",
        quality_profile_id=1,
        metadata_profile_id=2,
        root_dir="/music",
    )

    assert result["success"] is False
    assert "not found" in result["error"]
    mock_lidarr.artist.add.assert_not_called()


# ============================================================================
# get_recent_history — D-C3 lazy-poll source
# ============================================================================


@pytest.mark.asyncio
@patch("app.services.lidarr_client.Lidarr")
async def test_get_recent_history_returns_records(mock_lidarr_cls):
    """get_recent_history returns the records list from lidarr.get_history result."""
    from app.services.lidarr_client import get_recent_history

    mock_lidarr = MagicMock()
    mock_lidarr.history.get.return_value = {
        "records": [{"eventType": "trackFileImported", "artistId": 42}]
    }
    mock_lidarr_cls.return_value = mock_lidarr

    records = await get_recent_history("http://lidarr", "key", page_size=50)

    assert records == [{"eventType": "trackFileImported", "artistId": 42}]


@pytest.mark.asyncio
@patch("app.services.lidarr_client.Lidarr")
async def test_get_recent_history_tolerates_errors(mock_lidarr_cls):
    """Lidarr unreachable → returns empty list (best-effort lazy poll)."""
    from app.services.lidarr_client import get_recent_history

    mock_lidarr_cls.side_effect = ConnectionError("refused")

    records = await get_recent_history("http://lidarr", "key")

    assert records == []


# ============================================================================
# save_lidarr — extras dict shape (D-E1)
# ============================================================================


@pytest.fixture
def settings_db(test_engine):
    """Database with ServiceConfig table for settings tests."""
    from sqlmodel import Session, SQLModel
    from app.models.settings import ServiceConfig  # noqa: F401

    SQLModel.metadata.create_all(test_engine)
    with Session(test_engine) as session:
        yield session
    SQLModel.metadata.drop_all(test_engine)


def test_save_lidarr_persists_five_extras_keys(settings_db):
    """POST /api/settings/lidarr/save writes ALL 5 keys to ServiceConfig.extras."""
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.settings_service import get_setting

    with TestClient(app) as client:
        resp = client.post(
            "/api/settings/lidarr/save",
            data={
                "url": "http://lidarr",
                "api_key": "secret",
                "quality_profile_id": "1",
                "quality_profile_name": "FLAC",
                "metadata_profile_id": "2",
                "metadata_profile_name": "Standard",
                "root_folder_path": "/music",
            },
        )
        assert resp.status_code == 200

        from app.database import get_engine
        from sqlmodel import Session

        with Session(get_engine()) as s:
            setting = get_setting(s, "lidarr")
            assert setting is not None
            extras = setting.extra_config or {}
            assert extras.get("quality_profile_id") == "1"
            assert extras.get("quality_profile_name") == "FLAC"
            assert extras.get("metadata_profile_id") == "2"
            assert extras.get("metadata_profile_name") == "Standard"
            assert extras.get("root_folder_path") == "/music"


def test_save_lidarr_back_compat_mirrors_old_keys(settings_db):
    """save_lidarr also writes legacy 'profile_id'/'profile_name' keys
    (mirror of quality_profile_*) so legacy readers still work.
    """
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.settings_service import get_setting

    with TestClient(app) as client:
        resp = client.post(
            "/api/settings/lidarr/save",
            data={
                "url": "http://lidarr",
                "api_key": "secret",
                "quality_profile_id": "7",
                "quality_profile_name": "Lossless",
                "metadata_profile_id": "9",
                "metadata_profile_name": "Standard",
                "root_folder_path": "/music",
            },
        )
        assert resp.status_code == 200

        from app.database import get_engine
        from sqlmodel import Session

        with Session(get_engine()) as s:
            setting = get_setting(s, "lidarr")
            extras = setting.extra_config or {}
            # Back-compat: legacy keys mirror the quality_* values.
            assert extras.get("profile_id") == "7"
            assert extras.get("profile_name") == "Lossless"


# ============================================================================
# connection_status.html — 3 dropdowns + conditional root folder
# ============================================================================


def _render_connection_status(**ctx) -> str:
    """Render the partial with the provided context. Returns the HTML string."""
    template_dir = Path(__file__).parent.parent / "app" / "templates"
    templates = Jinja2Templates(directory=str(template_dir))
    template = templates.env.get_template("partials/connection_status.html")
    return template.render(**ctx)


def test_connection_status_partial_renders_metadata_dropdown():
    """Lidarr connection_status partial renders BOTH quality_profile_id AND
    metadata_profile_id select elements when success=True.
    """
    html = _render_connection_status(
        service="lidarr",
        success=True,
        url="http://lidarr",
        api_key="secret",
        quality_profiles=[{"id": 1, "name": "FLAC"}],
        metadata_profiles=[{"id": 2, "name": "Standard"}],
        root_folders=[{"id": 3, "path": "/music"}],
        error=None,
    )

    assert 'name="metadata_profile_id"' in html
    assert 'name="quality_profile_id"' in html


def test_connection_status_partial_renders_root_folder_only_when_multiple():
    """With root_folders length 1 → no <select name="root_folder_path">
    (auto-selected silently via hidden input). With length 2 → dropdown present.
    """
    # Single root — hidden input
    html_single = _render_connection_status(
        service="lidarr",
        success=True,
        url="http://lidarr",
        api_key="secret",
        quality_profiles=[{"id": 1, "name": "FLAC"}],
        metadata_profiles=[{"id": 2, "name": "Standard"}],
        root_folders=[{"id": 3, "path": "/music"}],
        error=None,
    )
    assert '<select id="lidarr-root"' not in html_single
    # Hidden input for the single path
    assert 'name="root_folder_path"' in html_single
    assert 'value="/music"' in html_single

    # Multiple roots — dropdown
    html_multi = _render_connection_status(
        service="lidarr",
        success=True,
        url="http://lidarr",
        api_key="secret",
        quality_profiles=[{"id": 1, "name": "FLAC"}],
        metadata_profiles=[{"id": 2, "name": "Standard"}],
        root_folders=[
            {"id": 3, "path": "/music"},
            {"id": 4, "path": "/music2"},
        ],
        error=None,
    )
    assert '<select id="lidarr-root"' in html_multi
    assert 'name="root_folder_path"' in html_multi
