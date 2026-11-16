from __future__ import annotations

import re
from urllib.parse import urlsplit

# Channel avatars come from the channel page og:image, which is a 900px original
# (`...=s900-c-k-c0x00ffffff-no-rj`). The list shows them at 28px, so request a small size.
CHANNEL_AVATAR_DISPLAY_SIZE = 88
_RESIZABLE_AVATAR_HOSTS = frozenset(
    {"yt3.googleusercontent.com", "yt3.ggpht.com", "lh3.googleusercontent.com"}
)
_SIZE_PARAM_RE = re.compile(r"=s\d+(?=-|$)")


def channel_avatar_display_url(raw_url: str | None, size: int = CHANNEL_AVATAR_DISPLAY_SIZE) -> str:
    """Return the avatar URL resized for list display; other URLs are returned unchanged."""
    url = str(raw_url or "").strip()
    if not url:
        return ""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return url
    if host not in _RESIZABLE_AVATAR_HOSTS:
        return url
    head, sep, params = url.rpartition("=")
    if not sep or "/" in params or "?" in params or "#" in params:
        return url
    resized, count = _SIZE_PARAM_RE.subn(f"=s{max(1, int(size))}", f"={params}", count=1)
    if count == 0:
        return url
    return head + resized
