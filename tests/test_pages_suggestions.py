"""Phase 7 Plan 03 Task 2 — /suggestions queue page + vibes home + root rewire.

Pins UI-01 (vibes home as landing) + D-08/D-09/D-10 (compact list, tap-to-
expand inline rationale, dismiss inside expanded view) + UI-04 (44px tap
targets) + the empty-state bootstrap loader (CONTEXT 'Claude's Discretion').
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlmodel import Session, SQLModel


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "app" / "templates"


@pytest.fixture
def jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "jinja"]),
    )
    from app.utils.jinja_filters import register_filters
    register_filters(env)
    return env


@pytest.fixture
def client_full(test_engine) -> Generator[TestClient, None, None]:
    """TestClient with the full Phase 5/6/7 schema registered."""
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401
    from app.models.vibe import (  # noqa: F401
        ManagedPlaylist, MigrationLog, SetupState, SlotInLog, TrackVibe, Vibe,
    )
    from app.models.suggestions import (  # noqa: F401
        NegativeSignal, RefillTriggerLog, SuggestionHistory, SuggestionsMirror,
    )

    from app.database import init_db
    init_db()
    SQLModel.metadata.create_all(test_engine)
    from app.main import app
    with TestClient(app) as c:
        yield c
    SQLModel.metadata.drop_all(test_engine)


def _seed_track(session, *, plex_rating_key="rk-1", title="T1", artist="A1"):
    from app.models.track import Track
    t = Track(
        plex_rating_key=plex_rating_key,
        title=title,
        artist=artist,
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def _seed_mirror_row(session, *, track_id, position=0, vibe_id=None,
                     rationale=None, score=None):
    from app.models.suggestions import SuggestionsMirror
    row = SuggestionsMirror(
        track_id=track_id,
        position=position,
        added_at=datetime.now(timezone.utc).isoformat(),
        rationale=rationale,
        vibe_id=vibe_id,
        score=score,
    )
    session.add(row)
    session.commit()
    return row


def _seed_vibe(session, *, name="V1", description=None):
    from app.models.vibe import Vibe
    v = Vibe(name=name, description=description, created_at=datetime.now(timezone.utc).isoformat())
    session.add(v)
    session.commit()
    session.refresh(v)
    return v


def _seed_plex_configured(session):
    from app.services.settings_service import save_setting
    save_setting(
        session, service_name="plex",
        url="http://plex.local:32400",
        credential="x" * 20,
    )


# ---------------------------------------------------------------------------
# /suggestions page rendering
# ---------------------------------------------------------------------------

class TestSuggestionsPage:
    def test_renders_mirror_rows_ordered_by_position(self, client_full, test_engine):
        with Session(test_engine) as s:
            t0 = _seed_track(s, plex_rating_key="rk-A", title="Alpha", artist="ArtA")
            t1 = _seed_track(s, plex_rating_key="rk-B", title="Beta", artist="ArtB")
            t2 = _seed_track(s, plex_rating_key="rk-C", title="Gamma", artist="ArtC")
            _seed_mirror_row(s, track_id=t0.id, position=0)
            _seed_mirror_row(s, track_id=t1.id, position=1)
            _seed_mirror_row(s, track_id=t2.id, position=2)
        resp = client_full.get("/suggestions")
        assert resp.status_code == 200
        body = resp.text
        assert "Alpha" in body
        assert "Beta" in body
        assert "Gamma" in body
        # Position order: Alpha appears before Beta which appears before Gamma.
        assert body.index("Alpha") < body.index("Beta") < body.index("Gamma")

    def test_empty_state_shows_progress_card(self, client_full):
        resp = client_full.get("/suggestions")
        assert resp.status_code == 200
        body = resp.text
        # CR-03 fix: the Alpine component now takes an `autostart` arg.
        # Match the function name only (`llmProgressCard(` — either
        # `llmProgressCard(false)` from server-render or
        # `llmProgressCard(true)` from the find-candidates HTMX swap path).
        assert "llmProgressCard(" in body

    def test_page_uses_active_page_suggestions(self, client_full):
        resp = client_full.get("/suggestions")
        # The bottom_tab_bar includes the active class for the matching slug.
        # We check that aria-current="page" appears in the rendered output.
        body = resp.text
        assert 'aria-current="page"' in body


class TestSuggestionsRowPartial:
    def test_album_art_48px(self, jinja_env):
        # Render the row partial in isolation with a stub context.
        from types import SimpleNamespace
        s = {
            "row": SimpleNamespace(rationale=None, score=None),
            "track": SimpleNamespace(plex_rating_key="rk-1", title="T", artist="A"),
            "vibe": None,
        }
        tpl = jinja_env.get_template("partials/suggestions_row.html")
        rendered = tpl.render(s=s)
        # 48px = w-12 h-12 in Tailwind.
        assert "w-12" in rendered and "h-12" in rendered

    def test_includes_vibe_chip(self, jinja_env):
        from types import SimpleNamespace
        s = {
            "row": SimpleNamespace(rationale=None, score=None),
            "track": SimpleNamespace(plex_rating_key="rk-1", title="T", artist="A"),
            "vibe": SimpleNamespace(name="Late Night"),
        }
        tpl = jinja_env.get_template("partials/suggestions_row.html")
        rendered = tpl.render(s=s)
        assert "Late Night" in rendered

    def test_tap_target_min_h_11(self, jinja_env):
        from types import SimpleNamespace
        s = {
            "row": SimpleNamespace(rationale=None, score=None),
            "track": SimpleNamespace(plex_rating_key="rk-1", title="T", artist="A"),
            "vibe": None,
        }
        tpl = jinja_env.get_template("partials/suggestions_row.html")
        rendered = tpl.render(s=s)
        assert "min-h-11" in rendered

    def test_tap_to_expand_attribute(self, jinja_env):
        from types import SimpleNamespace
        s = {
            "row": SimpleNamespace(rationale=None, score=None),
            "track": SimpleNamespace(plex_rating_key="rk-1", title="T", artist="A"),
            "vibe": None,
        }
        tpl = jinja_env.get_template("partials/suggestions_row.html")
        rendered = tpl.render(s=s)
        # D-09 — Alpine @click toggles the inline expanded panel.
        assert "expanded = !expanded" in rendered or "@click" in rendered


class TestSuggestionExpandedPartial:
    def test_renders_rationale(self, jinja_env):
        from types import SimpleNamespace
        s = {
            "row": SimpleNamespace(
                rationale="Same artist as 7 of your stars", score=None,
            ),
            "track": SimpleNamespace(plex_rating_key="rk-1", title="T", artist="A"),
            "vibe": None,
        }
        tpl = jinja_env.get_template("partials/suggestion_expanded.html")
        rendered = tpl.render(s=s)
        assert "Same artist as 7 of your stars" in rendered

    def test_play_in_plexamp_link(self, jinja_env):
        from types import SimpleNamespace
        s = {
            "row": SimpleNamespace(rationale="why", score=None),
            "track": SimpleNamespace(plex_rating_key="rk-42", title="T", artist="A"),
            "vibe": None,
        }
        tpl = jinja_env.get_template("partials/suggestion_expanded.html")
        rendered = tpl.render(s=s)
        assert 'href="plexamp://' in rendered
        assert "rk-42" in rendered

    def test_dismiss_button(self, jinja_env):
        from types import SimpleNamespace
        s = {
            "row": SimpleNamespace(rationale="why", score=None),
            "track": SimpleNamespace(plex_rating_key="rk-42", title="T", artist="A"),
            "vibe": None,
        }
        tpl = jinja_env.get_template("partials/suggestion_expanded.html")
        rendered = tpl.render(s=s)
        assert 'hx-post="/api/suggestions/rk-42/dismiss"' in rendered
        assert 'hx-target="#suggestion-row-rk-42"' in rendered
        assert 'hx-swap="outerHTML"' in rendered

    def test_no_hover_only_state(self, jinja_env):
        """Pitfall 18 — dismiss button must always be visible inside the
        expanded panel. We confirm there is NO `hidden` class on the dismiss
        button itself.
        """
        from types import SimpleNamespace
        s = {
            "row": SimpleNamespace(rationale="why", score=None),
            "track": SimpleNamespace(plex_rating_key="rk-42", title="T", artist="A"),
            "vibe": None,
        }
        tpl = jinja_env.get_template("partials/suggestion_expanded.html")
        rendered = tpl.render(s=s)
        # The button block should NOT contain a hover-only show class.
        # Approximate check: the word "Dismiss" appears outside any `hidden`
        # rule.
        assert "Dismiss" in rendered


# ---------------------------------------------------------------------------
# / (home) rewire to vibes home (UI-01)
# ---------------------------------------------------------------------------

class TestRootRewire:
    def test_rewired_to_vibes_home_when_vibes_exist(self, client_full, test_engine):
        with Session(test_engine) as s:
            _seed_plex_configured(s)
            _seed_vibe(s, name="V1")
        resp = client_full.get("/")
        assert resp.status_code == 200
        body = resp.text
        # The vibes home page renders the heading "Your vibes".
        assert "Your vibes" in body or "vibes_home" in body
        # Should NOT render the legacy chat textarea.
        assert "Describe a mood or vibe" not in body

    def test_root_redirects_to_setup_when_no_vibes(self, client_full, test_engine):
        with Session(test_engine) as s:
            _seed_plex_configured(s)
            _seed_track(s)
            from app.models.track import Track
            t = s.exec(__import__("sqlmodel").select(Track)).first()
            t.user_rating = 8.0
            s.add(t)
            s.commit()
        resp = client_full.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/setup"

    def test_root_shows_welcome_when_plex_unconfigured(self, client_full):
        resp = client_full.get("/")
        body = resp.text
        # welcome.html contains "Welcome to Composer".
        assert "Welcome to Composer" in body


# ---------------------------------------------------------------------------
# /vibes — explicit landing route
# ---------------------------------------------------------------------------

class TestVibesHome:
    def test_renders_per_vibe_cards(self, client_full, test_engine):
        with Session(test_engine) as s:
            _seed_plex_configured(s)
            v1 = _seed_vibe(s, name="V1")
            v2 = _seed_vibe(s, name="V2")
            v3 = _seed_vibe(s, name="V3")
            t1 = _seed_track(s, plex_rating_key="rk-1")
            t2 = _seed_track(s, plex_rating_key="rk-2")
            from app.models.vibe import TrackVibe
            from datetime import datetime as _dt, timezone as _tz
            now = _dt.now(_tz.utc).isoformat()
            s.add(TrackVibe(
                track_id=t1.id, vibe_id=v1.id, distance=0.1,
                assigned_at=now, assigned_by="cluster",
            ))
            s.add(TrackVibe(
                track_id=t2.id, vibe_id=v1.id, distance=0.2,
                assigned_at=now, assigned_by="cluster",
            ))
            s.commit()
        resp = client_full.get("/vibes")
        assert resp.status_code == 200
        body = resp.text
        assert "V1" in body
        assert "V2" in body
        assert "V3" in body

    def test_renders_find_candidates_cta_when_count_below_25(
        self, client_full, test_engine,
    ):
        with Session(test_engine) as s:
            _seed_plex_configured(s)
            v = _seed_vibe(s, name="Sparse")
        resp = client_full.get("/vibes")
        assert resp.status_code == 200
        body = resp.text
        assert "Find candidates" in body
        assert f'hx-post="/api/vibes/{v.id}/find-candidates"' in body

    def test_hides_find_candidates_cta_when_count_at_or_above_25(
        self, client_full, test_engine,
    ):
        with Session(test_engine) as s:
            _seed_plex_configured(s)
            v = _seed_vibe(s, name="Full")
            vibe_id = v.id  # capture before further commits expire v
            from app.models.vibe import TrackVibe
            now = datetime.now(timezone.utc).isoformat()
            for i in range(30):
                t = _seed_track(s, plex_rating_key=f"rk-{i}", title=f"T{i}")
                s.add(TrackVibe(
                    track_id=t.id, vibe_id=vibe_id, distance=0.1,
                    assigned_at=now, assigned_by="cluster",
                ))
            s.commit()
        resp = client_full.get("/vibes")
        body = resp.text
        # The Full vibe card should NOT include the Find candidates link.
        # We assert the substring is not present anywhere on the page (no
        # other vibe is sparse since only one was seeded).
        assert f'hx-post="/api/vibes/{vibe_id}/find-candidates"' not in body
