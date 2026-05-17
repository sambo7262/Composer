"""Phase 8 Plan 02 — ListenBrainz labs similar-artists client tests.

Task 1 (this file initial commit) writes the **parser fixture test** as the
TDD RED gate — it asserts the contract Task 2 must satisfy when it creates
``app/services/listenbrainz_client.py``. The fixture in
``tests/fixtures/listenbrainz_similar_artists_sample.json`` was captured at
Plan 02 Task 1 from a live ``labs.api.listenbrainz.org`` request for Four Tet
(MBID ``f6f2326f-6b25-4170-b89d-e235b25508e8``); if the upstream endpoint
shape changes, this fixture-vs-live diff is the canary.

Task 2 adds the remaining 4 behaviour tests
(``test_returns_empty_list_on_http_5xx``,
``test_returns_empty_list_on_timeout``,
``test_passes_seed_mbid_as_query_param``,
``test_respects_limit_param``).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "listenbrainz_similar_artists_sample.json"
)


def _load_fixture() -> list[dict]:
    with FIXTURE_PATH.open() as f:
        return json.load(f)


@pytest.mark.asyncio
async def test_parses_real_fixture(monkeypatch):
    """RED gate — when Task 2 creates listenbrainz_client.get_similar_artists,
    mocking httpx.AsyncClient.get to return the captured Four Tet fixture
    must yield a list of dicts each containing at minimum
    ``artist_mbid`` (str) and ``name`` (str). ``score`` is also present per
    RESEARCH §3 (the labs endpoint returns an int but the parser tolerates
    both int and float).
    """
    fixture = _load_fixture()
    assert isinstance(fixture, list)
    assert len(fixture) >= 5
    for entry in fixture:
        assert "artist_mbid" in entry and isinstance(entry["artist_mbid"], str)
        assert "name" in entry and isinstance(entry["name"], str)

    # Import is deferred so this file's collection doesn't crash before
    # Task 2 creates the module. The actual import-and-call happens
    # inside the test body so collection is decoupled from Task 2 status.
    from app.services import listenbrainz_client

    # Mock httpx.AsyncClient — the labs request itself never fires.
    fake_response = MagicMock()
    fake_response.json = MagicMock(return_value=fixture)
    fake_response.raise_for_status = MagicMock()
    fake_response.status_code = 200

    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=fake_response)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "app.services.listenbrainz_client.httpx.AsyncClient",
        lambda *args, **kwargs: fake_client,
    )

    result = await listenbrainz_client.get_similar_artists(
        "f6f2326f-6b25-4170-b89d-e235b25508e8",
    )

    assert isinstance(result, list)
    assert len(result) == len(fixture)
    for parsed, original in zip(result, fixture):
        assert parsed["artist_mbid"] == original["artist_mbid"]
        assert parsed["name"] == original["name"]
