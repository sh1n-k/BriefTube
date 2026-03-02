from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from playwright.sync_api import Page

from tests.e2e.seed_helpers import disable_all_workers, init_schema

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 15.0) -> None:
    import urllib.request
    import urllib.error

    url = f"http://127.0.0.1:{port}/healthz"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = urllib.request.urlopen(url, timeout=2)
            if resp.status == 200:
                return
        except (urllib.error.URLError, OSError, TimeoutError):
            pass
        time.sleep(0.3)
    raise RuntimeError(f"Server on port {port} did not start within {timeout}s")


@pytest.fixture(scope="module")
def e2e_server(tmp_path_factory: pytest.TempPathFactory):
    """Start an independent uvicorn server for each test module."""
    base = tmp_path_factory.mktemp("e2e")
    db_path = str(base / "test.db")
    thumbnail_dir = str(base / "thumbnails")
    download_dir = str(base / "downloads")
    log_dir = str(base / "logs")

    os.makedirs(thumbnail_dir, exist_ok=True)
    os.makedirs(download_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)

    init_schema(db_path)
    disable_all_workers(db_path)

    port = _find_free_port()

    env = {
        **os.environ,
        "DB_PATH": db_path,
        "THUMBNAIL_DIR": thumbnail_dir,
        "DOWNLOAD_DIR": download_dir,
        "LOG_DIR": log_dir,
        "LOG_TO_FILE": "false",
        "LLM_TIMEOUT_SECONDS": "120",
        "TELEGRAM_BOT_TOKEN": "",
        "TELEGRAM_CHAT_ID": "",
        "BRIEFTUBE_DISABLE_CHANNEL_METADATA_WORKER": "1",
        "BRIEFTUBE_LLM_RESPONSE_CAPTURE_DISABLED": "1",
    }

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "app.main:app",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--log-level", "warning",
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        _wait_for_server(port)
    except RuntimeError:
        proc.kill()
        stdout, stderr = proc.communicate(timeout=5)
        raise RuntimeError(
            f"Server failed to start.\nstdout: {stdout.decode()}\nstderr: {stderr.decode()}"
        )

    yield {
        "port": port,
        "base_url": f"http://127.0.0.1:{port}",
        "db_path": db_path,
        "thumbnail_dir": thumbnail_dir,
        "download_dir": download_dir,
        "process": proc,
    }

    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@pytest.fixture()
def e2e_page(e2e_server: dict, context) -> Page:
    """Provide a Playwright page pointed at the test server."""
    page = context.new_page()
    page.set_default_timeout(10_000)
    page.set_default_navigation_timeout(15_000)

    # Store base_url and db_path for easy access in tests
    page._e2e_base_url = e2e_server["base_url"]
    page._e2e_db_path = e2e_server["db_path"]
    page._e2e_server = e2e_server

    yield page
    page.close()


def pytest_collection_modifyitems(config, items):
    """Mark all tests under tests/e2e/ with the e2e marker."""
    for item in items:
        if "/tests/e2e/" in str(item.fspath):
            item.add_marker(pytest.mark.e2e)
