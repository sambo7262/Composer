"""Tests for app/services/rating_helpers.py — userRating display conversion (RATE-01)."""
from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "raw,expected",
    [
        (7.0, "3.5 stars"),
        (10.0, "5.0 stars"),
        (0.0, "0.0 stars"),
        (None, "0.0 stars"),
        (5.0, "2.5 stars"),
        (1.0, "0.5 stars"),
        (8.0, "4.0 stars"),
        (3.0, "1.5 stars"),
    ],
)
def test_stars_from_user_rating(raw, expected):
    """Plex stores userRating 0-10; helper converts to display 'N.N stars'.

    Per Pitfall 2: persist raw 0-10 in DB; only convert at display boundaries.
    7.0 → 3.5 stars is the canonical sanity check.
    """
    from app.services.rating_helpers import stars_from_user_rating

    assert stars_from_user_rating(raw) == expected
