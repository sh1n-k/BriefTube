from __future__ import annotations

from math import ceil


def build_pagination(
    *,
    total: int,
    page: int,
    limit: int,
    channel_id: str | None,
    sort: str,
    order: str,
) -> dict[str, object]:
    safe_limit = max(1, limit)
    total_pages = max(1, ceil(max(0, total) / safe_limit))
    current_page = min(max(1, page), total_pages)

    start = max(1, current_page - 2)
    end = min(total_pages, current_page + 2)

    # Keep a stable window size (up to 5 pages)
    while end - start < 4 and start > 1:
        start -= 1
    while end - start < 4 and end < total_pages:
        end += 1

    page_numbers = list(range(start, end + 1))

    return {
        "page": current_page,
        "limit": safe_limit,
        "total": max(0, total),
        "total_pages": total_pages,
        "channel_id": channel_id or "",
        "sort": sort,
        "order": order,
        "page_numbers": page_numbers,
        "first_page": 1,
        "last_page": total_pages,
        "prev_page": max(1, current_page - 1),
        "next_page": min(total_pages, current_page + 1),
        "has_prev": current_page > 1,
        "has_next": current_page < total_pages,
        "show_left_ellipsis": start > 2,
        "show_right_ellipsis": end < total_pages - 1,
    }
