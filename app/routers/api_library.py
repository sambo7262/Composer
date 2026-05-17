from __future__ import annotations

from math import ceil

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, or_, select, func, col

from app.database import get_session
from app.models.event_log import EventLog
from app.models.track import Track

router = APIRouter(prefix="/api/library", tags=["library"])

# T-02-05: Allowlisted sort columns to prevent injection
ALLOWED_SORT_COLUMNS = {"title", "artist", "album", "genre", "year"}
ALLOWED_ORDERS = {"asc", "desc"}

# T-02-07: Cap per_page to prevent DoS
MAX_PER_PAGE = 100

# Phase 8 Plan 04 (UI-07 / D-D1) — extended filter + sort allowlists.
# Mobile-first surfaces (filter chips + sort bottom sheet) need a wider
# vocabulary than the v1 wide-table-header sort links. Unknown values
# clamp to the defaults — mirrors the Phase 2 T-02-05 pattern.
ALLOWED_FILTERS = {"all", "analyzed", "unanalyzed", "rated"}
ALLOWED_SORTS = {"title", "artist", "added", "rating"}


def get_templates():
    """Lazy import to avoid circular dependency with app.main."""
    from app.main import templates
    return templates


@router.get("/tracks", response_class=HTMLResponse)
async def get_tracks(
    request: Request,
    session: Session = Depends(get_session),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=MAX_PER_PAGE),
    search: str = Query(default=""),
    sort: str = Query(default="title"),
    order: str = Query(default="asc"),
    filter: str = Query(default="all"),
):
    """Return paginated, searchable, sortable, filterable track list.

    T-02-05: sort validated against allowlist, order validated against asc/desc.
    T-02-07: per_page capped at 100, page >= 1.
    T-08-21 (Phase 8 Plan 04): ``filter`` validated against ALLOWED_FILTERS,
    ``sort`` accepts an extended vocabulary (title/artist/added/rating).
    Unknown values clamp to safe defaults.

    Returns ``partials/library_results_wrapper.html`` — the Phase 8
    single HTMX swap target that wraps both the sub-md card list AND
    the md+ wide table together.
    """
    templates = get_templates()

    # Phase 8 (T-08-21): filter allowlist — unknown clamps to "all"
    if filter not in ALLOWED_FILTERS:
        filter = "all"

    # Phase 8: sort allowlist extended for mobile sort sheet vocabulary.
    # Mobile sort vocabulary ("added"/"rating") is preferred when explicitly
    # passed; legacy wide-table sort vocabulary
    # ("album"/"genre"/"year") stays accepted for back-compat. Anything
    # else clamps to "title".
    legacy_sort = sort in ALLOWED_SORT_COLUMNS
    new_sort = sort in ALLOWED_SORTS
    if not (legacy_sort or new_sort):
        sort = "title"

    if order not in ALLOWED_ORDERS:
        order = "asc"

    # Build base query
    query = select(Track)

    # Apply search filter (T-02-05: parameterized ilike, no raw SQL)
    search = search.strip()
    if search:
        search_pattern = f"%{search}%"
        query = query.where(
            or_(
                Track.title.ilike(search_pattern),
                Track.artist.ilike(search_pattern),
                Track.album.ilike(search_pattern),
            )
        )

    # Phase 8 — filter chip vocabulary
    if filter == "analyzed":
        query = query.where(Track.energy.is_not(None))  # type: ignore[union-attr]
    elif filter == "unanalyzed":
        query = query.where(Track.energy.is_(None))  # type: ignore[union-attr]
    elif filter == "rated":
        query = query.where(Track.user_rating > 0)  # type: ignore[arg-type]

    # Count total results
    count_query = select(func.count()).select_from(query.subquery())
    total = session.exec(count_query).one()

    # Apply sorting — mobile vocabulary maps to specific columns + orders
    # so the user gets the intuitive ordering on tap (newest-added first,
    # highest-rated first). The legacy wide-table headers pass an
    # explicit ``order`` so they continue to flip asc/desc on toggle.
    if sort == "rating":
        # Phase 8 mobile vocabulary — rating sort is always DESC so the
        # top-rated tracks bubble up; null user_ratings sort last.
        query = query.order_by(
            col(Track.user_rating).desc().nullslast(),
        )
    elif sort == "added":
        query = query.order_by(col(Track.added_at).desc().nullslast())
    else:
        sort_column = getattr(Track, sort, Track.title)
        if order == "desc":
            query = query.order_by(col(sort_column).desc())
        else:
            query = query.order_by(col(sort_column).asc())

    # Apply pagination
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)

    tracks = session.exec(query).all()

    # Pagination metadata
    total_pages = ceil(total / per_page) if per_page > 0 else 1
    has_prev = page > 1
    has_next = page < total_pages

    context = {
        "tracks": tracks,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": has_prev,
        "has_next": has_next,
        "search": search,
        "sort": sort,
        "order": order,
        "filter": filter,
    }

    # Phase 8 — single swap target. The wrapper renders BOTH the mobile
    # card list AND the md+ wide table so one outerHTML swap covers both
    # breakpoints (no need to do a viewport-aware client-side decision).
    return templates.TemplateResponse(
        request,
        "partials/library_results_wrapper.html",
        context,
    )


@router.get("/stats", response_class=HTMLResponse)
async def library_stats(request: Request, session: Session = Depends(get_session)):
    """Library-at-a-glance partial (Concern 1 / ROADMAP Phase 5 SC-5).

    Returns total_tracks, rated_tracks, last_rating_event_at as a small HTMX
    partial. Polled every 10s by both /settings and /debug/events so the rated
    count visibly increments as media.rate webhooks arrive.

    Aggregates run in <1ms on SQLite even for 10k-row tables — no caching.
    Track.user_rating > 0 uses the ix_track_user_rating index landed in Plan 01
    (RATE-04).
    """
    templates = get_templates()
    total_tracks = session.exec(select(func.count(Track.id))).one() or 0
    rated_tracks = (
        session.exec(
            select(func.count(Track.id)).where(Track.user_rating > 0)  # type: ignore[arg-type]
        ).one()
        or 0
    )
    last_event = session.exec(
        select(EventLog.received_at)
        .where(EventLog.event_type == "rating_changed")
        .order_by(col(EventLog.received_at).desc())
        .limit(1)
    ).first()
    return templates.TemplateResponse(
        request,
        "partials/library_stats.html",
        {
            "total_tracks": total_tracks,
            "rated_tracks": rated_tracks,
            "last_rating_event_at": last_event,
        },
    )
