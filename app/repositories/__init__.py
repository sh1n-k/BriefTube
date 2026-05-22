"""Domain repository boundary modules.

Application code should import the specific repository domain it needs, for
example `app.repositories.videos` or `app.repositories.channels`.
"""

from app.repositories import (
    alerts_retention,
    categories,
    channels,
    downloads,
    llm,
    manual_articles,
    manual_transcripts,
    settings,
    transcripts,
    videos,
)

__all__ = [
    "alerts_retention",
    "categories",
    "channels",
    "downloads",
    "llm",
    "manual_articles",
    "manual_transcripts",
    "settings",
    "transcripts",
    "videos",
]
