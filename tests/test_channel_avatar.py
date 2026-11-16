from __future__ import annotations

import pytest

from app.services.channel_avatar import channel_avatar_display_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://yt3.googleusercontent.com/abc=s900-c-k-c0x00ffffff-no-rj",
            "https://yt3.googleusercontent.com/abc=s88-c-k-c0x00ffffff-no-rj",
        ),
        ("https://yt3.ggpht.com/abc=s176-c-k", "https://yt3.ggpht.com/abc=s88-c-k"),
        ("https://lh3.googleusercontent.com/abc=s900", "https://lh3.googleusercontent.com/abc=s88"),
        # Not a resizable host or no size parameter: keep the URL as is.
        ("https://i.ytimg.com/vi/test/hqdefault.jpg", "https://i.ytimg.com/vi/test/hqdefault.jpg"),
        ("https://yt3.googleusercontent.com/abc", "https://yt3.googleusercontent.com/abc"),
        (
            "https://yt3.googleusercontent.com/abc=w900-h900",
            "https://yt3.googleusercontent.com/abc=w900-h900",
        ),
        ("https://evil.example/x=s900", "https://evil.example/x=s900"),
        ("", ""),
        (None, ""),
    ],
)
def test_channel_avatar_display_url(raw: str | None, expected: str) -> None:
    assert channel_avatar_display_url(raw) == expected
