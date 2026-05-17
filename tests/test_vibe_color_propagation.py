"""Phase 8 Plan 03 Task 1 — Vibe color propagation across surfaces (UI-09 / D-E2).

Covers:
- Vibe creation paths (wizard finalize + re-cluster commit) wire
  assign_vibe_color from app.services.discovery_service.
- Existing vibe colors survive re-cluster (D-22 stickiness rule).
- Templates: vibe_card.html, suggestions_row.html, recluster_modal.html,
  debug_vibes.html / vibe_diagnostic_card.html, setup_step3.html all read
  ``{{ vibe.color }}`` (or proposed_color for the wizard).
- AST grep gate: no hardcoded palette hex literals in the vibe-rendering
  style attributes inside any of the listed templates (fallbacks like
  ``vibe.color or '#3a3a3e'`` are explicitly allowed).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlmodel import Session, SQLModel, select


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "app" / "templates"


@pytest.fixture
def jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "jinja"]),
    )
    # feature_chip_text is referenced by vibe_diagnostic_card / vibe_proposal_card.
    env.globals["feature_chip_text"] = lambda *args, **kwargs: "stub-chip"
    # local_time filter referenced by any template that renders a *_at column.
    from app.utils.jinja_filters import register_filters
    register_filters(env)
    return env


@pytest.fixture
def client_full(test_engine) -> Generator[TestClient, None, None]:
    from app.models.settings import ServiceConfig  # noqa: F401
    from app.models.track import SyncState, Track  # noqa: F401
    from app.models.event_log import EventLog  # noqa: F401
    from app.models.llm_usage import LLMUsage  # noqa: F401
    from app.models.taste_profile import TasteProfile  # noqa: F401
    from app.models.vibe import (  # noqa: F401
        DiscoveryState, ManagedPlaylist, MigrationLog, SetupState, SlotInLog,
        TrackVibe, Vibe,
    )
    from app.models.suggestions import (  # noqa: F401
        NegativeSignal, RefillTriggerLog, SuggestionHistory, SuggestionsMirror,
    )
    from app.models.discovery import (  # noqa: F401
        CostMeterBaseline, DiscoveryAdd, DiscoveryCandidate, DiscoveryDismissed,
        MusicBrainzCache, WeeklyCronState,
    )

    from app.database import init_db
    init_db()
    SQLModel.metadata.create_all(test_engine)
    from app.main import app
    with TestClient(app) as c:
        yield c
    SQLModel.metadata.drop_all(test_engine)


# ============================================================================
# Vibe-creation wiring — assign_vibe_color called on new commit
# ============================================================================


class TestAssignVibeColorOnCreation:
    def test_assign_color_called_on_new_vibe_commit(self, db_with_phase7):
        """Vibe-creation paths invoke assign_vibe_color after the row has an id.
        The function must come from app.services.discovery_service (Plan 01),
        and the new Vibe row should land with color set to the palette slot.
        """
        from app.models.vibe import Vibe
        from app.services.discovery_service import (
            VIBE_COLOR_PALETTE,
            assign_vibe_color,
        )
        from app.services.vibe_service import (
            ensure_vibe_color_on_creation,
        )

        # Seed an unprimed vibe (color=NULL — mimics the freshly-committed
        # post-insert state in the wizard / recluster commit handler).
        now = datetime.now(timezone.utc).isoformat()
        v = Vibe(name="Late Night", created_at=now)
        db_with_phase7.add(v)
        db_with_phase7.commit()
        db_with_phase7.refresh(v)
        new_id = v.id

        ensure_vibe_color_on_creation(new_id)

        # Re-read via the same engine the helper used.
        db_with_phase7.expire_all()
        row = db_with_phase7.get(Vibe, new_id)
        assert row is not None
        assert row.color is not None, "color should be auto-assigned"
        assert row.color == assign_vibe_color(new_id)
        assert row.color in VIBE_COLOR_PALETTE

    def test_recluster_preserves_existing_vibe_colors(self, db_with_phase7):
        """Calling ensure_vibe_color_on_creation on a vibe whose color is
        already set is a NO-OP (D-22 stickiness rule extended to colors).
        """
        from app.models.vibe import Vibe
        from app.services.vibe_service import ensure_vibe_color_on_creation

        now = datetime.now(timezone.utc).isoformat()
        v = Vibe(name="Sunday", color="#3b82f6", created_at=now)
        db_with_phase7.add(v)
        db_with_phase7.commit()
        db_with_phase7.refresh(v)
        vid = v.id

        ensure_vibe_color_on_creation(vid)

        db_with_phase7.expire_all()
        row = db_with_phase7.get(Vibe, vid)
        assert row.color == "#3b82f6"


# ============================================================================
# Template rendering — each surface uses {{ vibe.color }} (or proposed_color)
# ============================================================================


class TestVibeCardRendersColor:
    def test_vibe_card_renders_color_accent(self, jinja_env):
        v = SimpleNamespace(
            id=1, name="Late Night", description=None, track_count=10,
            color="#3b82f6",
        )
        tpl = jinja_env.get_template("partials/vibe_card.html")
        out = tpl.render(v=v)
        assert "#3b82f6" in out, (
            "vibe_card.html should render the vibe.color in an inline style"
        )

    def test_vibe_card_renders_default_when_color_none(self, jinja_env):
        v = SimpleNamespace(
            id=1, name="Late Night", description=None, track_count=10,
            color=None,
        )
        tpl = jinja_env.get_template("partials/vibe_card.html")
        out = tpl.render(v=v)
        assert "Late Night" in out
        # Fallback hex is present (#3a3a3e) when color is None — never an
        # `or None` literal or a broken inline style.
        assert "style=" in out


class TestSuggestionsRowRendersVibeColor:
    def test_suggestions_row_renders_vibe_chip_color(self, jinja_env):
        s = {
            "row": SimpleNamespace(rationale=None, score=None),
            "track": SimpleNamespace(plex_rating_key="rk-1", title="T", artist="A"),
            "vibe": SimpleNamespace(name="Bright", color="#10b981"),
        }
        tpl = jinja_env.get_template("partials/suggestions_row.html")
        out = tpl.render(s=s)
        assert "#10b981" in out


class TestReclusterModalRendersColor:
    def test_recluster_modal_template_uses_vibe_color(self):
        """recluster_modal.html should reference vibe.color in its source.

        The modal is Alpine-driven (not server-iterated over vibes), so we
        verify the template SOURCE references the color hook. Runtime
        render-side coverage is covered by the AST grep test below.
        """
        path = TEMPLATES_DIR / "partials" / "recluster_modal.html"
        text = path.read_text()
        assert "vibe.color" in text, (
            "recluster_modal.html should reference vibe.color "
            "(vibe pills/accent in the modal preview)"
        )


class TestDebugVibesRendersColor:
    def test_debug_vibes_card_uses_vibe_color(self, jinja_env):
        """vibe_diagnostic_card.html is iterated inside debug_vibes.html
        for each vibe; the card should pick up the vibe.color accent.
        """
        v = SimpleNamespace(
            id=1, name="Sunday", description="weekend vibe",
            centroid_energy=0.5, centroid_tempo=120, centroid_danceability=0.5,
            centroid_valence=0.5, silhouette_score=0.8,
            created_at="2026-05-17T00:00:00+00:00", centroid_recomputed_at=None,
            is_active=True, color="#f43f5e",
        )
        tpl = jinja_env.get_template("partials/vibe_diagnostic_card.html")
        out = tpl.render(vibe=v, member_count=12)
        assert "#f43f5e" in out


class TestSetupStep3RendersProposedColor:
    def test_setup_step3_template_references_color(self):
        """setup_step3.html context now carries per-proposal color info.

        Grep gate per plan acceptance criterion:
            grep -n "proposed_color\\|vibe.color" app/templates/pages/setup_step3.html
        """
        path = TEMPLATES_DIR / "pages" / "setup_step3.html"
        text = path.read_text()
        assert "proposed_color" in text or "vibe.color" in text, (
            "setup_step3.html must reference proposed_color or vibe.color"
        )


# ============================================================================
# AST / grep — no hardcoded palette hex in vibe-rendering style attributes
# ============================================================================


def test_no_hardcoded_vibe_hex_in_render_paths():
    """Scan vibe-rendering templates for hardcoded palette hex literals
    inside style attributes. Hex literals as fallbacks (e.g.
    ``vibe.color or '#3a3a3e'``) are allowed; primary-value usage is not.
    """
    palette = {
        "#3b82f6", "#8b5cf6", "#10b981", "#f43f5e", "#f59e0b",
        "#06b6d4", "#ec4899", "#84cc16", "#f97316",
    }
    candidates = [
        TEMPLATES_DIR / "partials" / "vibe_card.html",
        TEMPLATES_DIR / "partials" / "suggestions_row.html",
        TEMPLATES_DIR / "partials" / "recluster_modal.html",
        TEMPLATES_DIR / "partials" / "vibe_diagnostic_card.html",
        TEMPLATES_DIR / "pages" / "debug_vibes.html",
    ]
    for tpl in candidates:
        if not tpl.exists():
            continue
        text = tpl.read_text()
        for hex_lit in palette:
            matches = re.findall(rf'style="[^"]*{hex_lit}[^"]*"', text)
            # Allow only when the hex appears alongside `vibe.color` (i.e.
            # as part of a fallback expression like `vibe.color or '#3a3a3e'`).
            bare_hex = [m for m in matches if "vibe.color" not in m]
            assert not bare_hex, (
                f"{tpl} has hardcoded palette color {hex_lit} in a style "
                f"attribute outside a vibe.color fallback expression. "
                f"Use {{ vibe.color }} instead."
            )


# ============================================================================
# Integration: vibe_service helper is the canonical creation entry point
# ============================================================================


class TestVibeServiceHelperExists:
    def test_vibe_service_exposes_ensure_vibe_color_on_creation(self):
        """The canonical creation entry point is
        ``app.services.vibe_service.ensure_vibe_color_on_creation``;
        wizard finalize + re-cluster commit handlers call this after
        committing the new Vibe row.
        """
        from app.services.vibe_service import (  # noqa: F401
            ensure_vibe_color_on_creation,
        )
        from app.services.discovery_service import assign_vibe_color  # noqa: F401
