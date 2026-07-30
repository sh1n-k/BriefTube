from __future__ import annotations

from app.repositories._common import (
    normalize_error_message,
    row_to_dict,
    rows_to_dicts,
    thumbnail_url,
    with_thumbnail_url,
)


def test_normalize_error_message_trims_and_truncates() -> None:
    assert normalize_error_message(None) == ""
    assert normalize_error_message("  hello  ") == "hello"
    assert normalize_error_message("x" * 10, max_length=4) == "xxxx"


def test_thumbnail_url_fallbacks_and_local_path() -> None:
    assert thumbnail_url(None) is None
    assert thumbnail_url(None, "abc123") == "https://i.ytimg.com/vi/abc123/hqdefault.jpg"
    assert thumbnail_url("/tmp/thumbs/vid.jpg") == "/thumbnails/vid.jpg"
    assert thumbnail_url("", "abc123") == "https://i.ytimg.com/vi/abc123/hqdefault.jpg"


def test_with_thumbnail_url_mutates_item() -> None:
    item = {"video_id": "v1", "thumbnail_path": None}
    assert with_thumbnail_url(item)["thumbnail_url"] == "https://i.ytimg.com/vi/v1/hqdefault.jpg"


def test_row_helpers_handle_none_and_mapping_rows() -> None:
    assert row_to_dict(None) is None

    class _Row(dict):
        def keys(self):
            return super().keys()

        def __getitem__(self, key):
            return super().__getitem__(key)

    row = _Row(a=1, b="x")
    assert row_to_dict(row) == {"a": 1, "b": "x"}
    assert rows_to_dicts([row]) == [{"a": 1, "b": "x"}]
