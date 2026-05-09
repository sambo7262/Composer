"""Rating display helpers (RATE-01).

Plex stores `userRating` on a 0-10 scale internally to support half-stars.
A userRating of 7.0 means 3.5 stars in the UI. We persist the RAW 0-10 value
in SQLite (Pitfall 2) and only convert at display boundaries via these helpers.
"""
from __future__ import annotations

from typing import Optional


def stars_from_user_rating(raw: Optional[float]) -> str:
    """Convert raw Plex userRating (0-10 scale) to display string '<n.n> stars'.

    Examples:
        7.0  -> "3.5 stars"  (canonical RATE-01 sanity check)
        10.0 -> "5.0 stars"
        0.0  -> "0.0 stars"
        None -> "0.0 stars"  (unrated)
    """
    stars = (raw or 0.0) / 2.0
    return f"{stars:.1f} stars"
