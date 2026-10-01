from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from fastapi.testclient import TestClient


def call_repo(
    client: TestClient,
    fn: Callable[..., Awaitable[Any]],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run an async repository function on the app's event loop with the app DB connection."""
    return client.portal.call(partial(fn, client.app.state.runtime.db, *args, **kwargs))
