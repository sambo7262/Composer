"""Phase 8 Plan 02 — ListenBrainz labs similar-artists client tests.

Task 1 wrote the **parser fixture test** as the TDD RED gate (it asserted the
contract Task 2 must satisfy). Task 2 extends with the remaining 4 behaviour
tests (HTTP 5xx tolerance, timeout tolerance, query-param shape, limit cap).

Fixture in ``tests/fixtures/listenbrainz_similar_artists_sample.json`` was
captured at Plan 02 Task 1 from a live ``labs.api.listenbrainz.org`` request
for Four Tet (MBID ``f6f2326f-6b25-4170-b89d-e235b25508e8``); if the upstream
endpoint shape changes, this fixture-vs-live diff is the canary.

**Locked parser contract** (Task 1 user approval): the labs endpoint returns
``score`` as an **integer** (raw value, NOT normalized to 0..1). The parser
preserves the raw value and the list-position ordering from ListenBrainz.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "listenbrainz_similar_artists_sample.json"
)


def _load_fixture() -> list[dict]:
    with FIXTURE_PATH.open() as f:
        return json.load(f)


def _make_fake_httpx_client(
    *,
    json_value=None,
    status_code: int = 200,
    raise_for_status_exc: Exception = None,
    get_exc: Exception = None,
):
    """Build a MagicMock pretending to be ``httpx.AsyncClient()``.

    - ``json_value`` is returned by ``resp.json()``.
    - ``raise_for_status_exc`` (if set) is raised by ``resp.raise_for_status()``.
    - ``get_exc`` (if set) is raised by ``client.get()`` (used to simulate
      ``httpx.TimeoutException`` / ``RequestError``).
    """
    fake_response = MagicMock()
    fake_response.json = MagicMock(return_value=json_value or [])
    if raise_for_status_exc is not None:
        fake_response.raise_for_status = MagicMock(
            side_effect=raise_for_status_exc,
        )
    else:
        fake_response.raise_for_status = MagicMock()
    fake_response.status_code = status_code

    fake_client = MagicMock()
    if get_exc is not None:
        fake_client.get = AsyncMock(side_effect=get_exc)
    else:
        fake_client.get = AsyncMock(return_value=fake_response)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    return fake_client


@pytest.mark.asyncio
async def test_parses_real_fixture(monkeypatch):
    """RED gate — when Task 2 creates listenbrainz_client.get_similar_artists,
    mocking httpx.AsyncClient.get to return the captured Four Tet fixture
    must yield a list of dicts each containing at minimum
    ``artist_mbid`` (str) and ``name`` (str). ``score`` is also present per
    RESEARCH §3 (the labs endpoint returns an int — the parser preserves it
    as-is, per Task 1 user-approved contract).
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

    fake_client = _make_fake_httpx_client(json_value=fixture)
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


@pytest.mark.asyncio
async def test_returns_empty_list_on_http_5xx(monkeypatch):
    """A 503 (or any HTTPStatusError) from the labs endpoint MUST return ``[]``
    rather than propagate. Discovery cron must never crash on one seed's
    lookup failure.
    """
    from app.services import listenbrainz_client

    # Build a fake response that .raise_for_status() raises 503 on.
    fake_response = MagicMock()
    fake_response.status_code = 503
    fake_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "503 Service Unavailable",
            request=MagicMock(),
            response=MagicMock(status_code=503),
        )
    )
    fake_response.json = MagicMock(return_value=[])
    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=fake_response)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "app.services.listenbrainz_client.httpx.AsyncClient",
        lambda *args, **kwargs: fake_client,
    )

    result = await listenbrainz_client.get_similar_artists("any-mbid")
    assert result == []


@pytest.mark.asyncio
async def test_returns_empty_list_on_timeout(monkeypatch):
    """``httpx.TimeoutException`` from the GET MUST return ``[]``."""
    from app.services import listenbrainz_client

    fake_client = _make_fake_httpx_client(
        get_exc=httpx.TimeoutException("timed out"),
    )
    monkeypatch.setattr(
        "app.services.listenbrainz_client.httpx.AsyncClient",
        lambda *args, **kwargs: fake_client,
    )

    result = await listenbrainz_client.get_similar_artists("any-mbid")
    assert result == []


@pytest.mark.asyncio
async def test_passes_seed_mbid_as_query_param(monkeypatch):
    """The labs URL must be hit with the seed MBID in the ``artist_mbids``
    query param (verifies the URL construction shape).
    """
    from app.services import listenbrainz_client

    fake_response = MagicMock()
    fake_response.json = MagicMock(return_value=[])
    fake_response.raise_for_status = MagicMock()
    fake_response.status_code = 200

    captured_calls = []

    async def _capture_get(url, **kwargs):
        captured_calls.append((url, kwargs))
        return fake_response

    fake_client = MagicMock()
    fake_client.get = _capture_get
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "app.services.listenbrainz_client.httpx.AsyncClient",
        lambda *args, **kwargs: fake_client,
    )

    seed = "f6f2326f-6b25-4170-b89d-e235b25508e8"
    await listenbrainz_client.get_similar_artists(seed)

    assert len(captured_calls) == 1
    url, kwargs = captured_calls[0]
    # The URL itself is the labs endpoint
    assert "labs.api.listenbrainz.org/similar-artists" in url
    # The seed MBID lands in the params dict under 'artist_mbids'
    assert kwargs.get("params", {}).get("artist_mbids") == seed
    # The default algorithm string is wired through
    assert "algorithm" in kwargs.get("params", {})


@pytest.mark.asyncio
async def test_respects_limit_param(monkeypatch):
    """``get_similar_artists(seed, limit=2)`` returns at most 2 entries even
    when the fixture has more — caller-side bound on the response slice.
    """
    from app.services import listenbrainz_client

    fixture = _load_fixture()
    assert len(fixture) >= 3, "fixture too small to exercise limit"

    fake_client = _make_fake_httpx_client(json_value=fixture)
    monkeypatch.setattr(
        "app.services.listenbrainz_client.httpx.AsyncClient",
        lambda *args, **kwargs: fake_client,
    )

    result = await listenbrainz_client.get_similar_artists(
        "any-mbid", limit=2,
    )
    assert len(result) == 2
