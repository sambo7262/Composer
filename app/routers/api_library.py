from __future__ import annotations

from math import ceil

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, or_, select, func, col

from app.database import get_session
from app.models.track import Track

router = APIRouter(prefix="/api/library", tags=["library"])

# T-02-05: Allowlisted sort columns to prevent injection
ALLOWED_SORT_COLUMNS = {"title", "artist", "album", "genre", "year"}
ALLOWED_ORDERS = {"asc", "desc"}

# T-02-07: Cap per_page to prevent DoS
MAX_PER_PAGE = 100


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
):
    """Return paginated, searchable, sortable track list as HTML partial.

    T-02-05: sort validated against allowlist, order validated against asc/desc.
    T-02-07: per_page capped at 100, page >= 1.
    """
    templates = get_templates()

    # Validate sort column against allowlist (T-02-05)
    if sort not in ALLOWED_SORT_COLUMNS:
        sort = "title"

    # Validate order (T-02-05)
    if order not in ALLOWED_ORDERS:
        order = "asc"

    # Build base query
    query = select(Track)

    # Apply search filter (T-02-05: uses parameterized ilike, no raw SQL)
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

    # Count total results
    count_query = select(func.count()).select_from(query.subquery())
    total = session.exec(count_query).one()

    # Apply sorting
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
    }

    # Check if HTMX request -- return partial only
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request,
            "partials/track_table.html",
            context,
        )

    # Full page request -- redirect to /library or return full page
    # For API endpoint, return the partial (full page is served by /library route)
    return templates.TemplateResponse(
        request,
        "partials/track_table.html",
        context,
    )
