"""GAP-02 closure regression test — library_stats partial polls
/api/library/stats at most every 10s, NOT 25-50 times per second.

Predates Phase 7.1; bundled into 07.1 gap closure per user direction
(see .planning/phases/07.1-.../07.1-VERIFICATION.md gaps frontmatter).

The bug was a syntactic ``hx-trigger="load, every 10s"`` in
``app/templates/partials/library_stats.html`` combined with
``hx-swap="outerHTML"``: every swap replaced the element with a new
copy that ALSO carried ``load`` in its trigger, so HTMX fired ``load``
immediately on the new element, infinite loop. The fix removes the
``load,`` prefix so the partial polls only on the ``every 10s`` cadence;
the initial render is initiated by the parent page's own ``load``
trigger (settings.html:9, debug_events.html:6, debug_vibes.html:9).

These tests catch both syntactic regression (someone re-adds
``load,``) and behavioral regression (a different infinite-loop
trigger sneaks in via a future change).
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel


@pytest.fixture
def client(test_engine):
    """Create a test client with fresh database.

    Local copy of the canonical ``client(test_engine)`` fixture from
    ``tests/test_library_api.py:14-27`` (per plan-checker correction in
    07.1-05-PLAN.md — ``tests/conftest.py`` does NOT expose a ``client``
    fixture; do NOT promote this to conftest in this plan).
    """
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401

    SQLModel.metadata.create_all(test_engine)

    from app.main import app

    with TestClient(app) as c:
        yield c

    SQLModel.metadata.drop_all(test_engine)


class TestLibraryStatsPartialPolling:
    """Regression guard for GAP-02 (NAS UAT 2026-05-16 07:15:53)."""

    def test_partial_contains_polling_only_trigger(self, client):
        """The partial's hx-trigger MUST be exactly 'every 10s' — never
        'load, every 10s' (the infinite-loop trigger that combined with
        hx-swap='outerHTML' fired 25-50 req/s against /api/library/stats
        on the NAS during ambient browsing)."""
        response = client.get("/api/library/stats")
        assert response.status_code == 200
        body = response.text
        assert 'hx-trigger="every 10s"' in body, (
            "library_stats.html must use polling-only trigger; "
            "see .planning/phases/07.1-.../07.1-VERIFICATION.md GAP-02"
        )
        # Catch syntactic re-introductions of the infinite-loop trigger.
        assert 'hx-trigger="load, every 10s"' not in body
        assert 'hx-trigger="load,every 10s"' not in body
        # Defensive — catch any `load,` prefix on hx-trigger.
        assert re.search(r'hx-trigger="load,\s*', body) is None, (
            "library_stats.html hx-trigger must not start with 'load,' "
            "— outerHTML swap + load trigger = infinite loop "
            "(GAP-02 evidence: NAS UAT 2026-05-16 07:15:53)"
        )

    def test_partial_preserves_outerhtml_swap_target(self, client):
        """The self-replacement pattern (outerHTML swap with the card's
        own id as target) is preserved — only the trigger string changes.
        This minimises blast radius and means the 3 parent pages that
        embed the partial (settings, debug_events, debug_vibes) need no
        changes."""
        response = client.get("/api/library/stats")
        assert response.status_code == 200
        body = response.text
        assert 'id="library-stats-card"' in body
        assert 'hx-target="#library-stats-card"' in body
        assert 'hx-swap="outerHTML"' in body
        assert 'hx-get="/api/library/stats"' in body

    def test_partial_labels_still_render(self, client):
        """Defensive — mirrors the existing TestStatsEndpoint label
        assertions so a single-file pytest run on this module catches
        accidental partial-body breakage."""
        response = client.get("/api/library/stats")
        assert response.status_code == 200
        body = response.text
        assert "Total tracks" in body
        assert "Rated tracks" in body
        assert "Last rating event" in body

    def test_partial_response_is_idempotent_across_rapid_calls(self, client):
        """Server-side proof that the infinite-loop trigger cannot
        self-propagate: three sequential calls to /api/library/stats
        return the polling-only trigger string and complete quickly.

        Note: a true end-to-end test of htmx polling cadence requires a
        headless browser. The combination of the trigger-string
        assertion in ``test_partial_contains_polling_only_trigger`` and
        this idempotency check is the strongest server-side proof
        available without browser automation. Browser-side polling
        cadence is covered by manual NAS UAT (see HUMAN-UAT item #1)."""
        import time

        t0 = time.monotonic()
        r1 = client.get("/api/library/stats")
        r2 = client.get("/api/library/stats")
        r3 = client.get("/api/library/stats")
        elapsed = time.monotonic() - t0

        assert r1.status_code == r2.status_code == r3.status_code == 200
        # All three responses contain the polling-only trigger (the bug
        # would have shown `load, every` instead). Three sequential
        # calls without state change is the closest server-side signal
        # that the infinite-loop trigger cannot self-propagate.
        for r in (r1, r2, r3):
            assert 'hx-trigger="every 10s"' in r.text
            assert 'hx-trigger="load,' not in r.text

        # Sanity: three calls completed quickly (no hanging on a
        # truly unbounded loop).
        assert elapsed < 5.0, (
            f"3 sequential /api/library/stats calls took {elapsed}s "
            "— route may be doing more work than expected"
        )
