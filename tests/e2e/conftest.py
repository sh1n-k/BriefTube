from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from playwright.sync_api import Page

from app.worker_registry import WORKER_SPECS
from tests.e2e.seed_helpers import disable_all_workers, init_schema

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_ALLOWLIST_KEYS = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LOCALAPPDATA",
    "PATHEXT",
    "PYTHONPATH",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "COMSPEC",
)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 15.0) -> None:
    import urllib.error
    import urllib.request

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


def _build_e2e_env(
    *,
    db_path: str,
    thumbnail_dir: str,
    download_dir: str,
    log_dir: str,
) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in ENV_ALLOWLIST_KEYS:
        value = os.environ.get(key)
        if value:
            env[key] = value
    env.update(
        {
            "DB_PATH": db_path,
            "THUMBNAIL_DIR": thumbnail_dir,
            "DOWNLOAD_DIR": download_dir,
            "LOG_DIR": log_dir,
            "LOG_TO_FILE": "false",
            "LLM_TIMEOUT_SECONDS": "120",
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_CHAT_ID": "",
            "BRIEFTUBE_REMOTE_SYNC_ENABLED": "0",
            "BRIEFTUBE_REMOTE_SYNC_DSN": "",
            "BRIEFTUBE_LLM_RESPONSE_CAPTURE_DISABLED": "1",
        }
    )
    env.update({spec.disable_env_name: "1" for spec in WORKER_SPECS})
    return env


def _start_server(
    *,
    env: dict[str, str],
    max_attempts: int = 3,
) -> tuple[subprocess.Popen[bytes], int]:
    last_error: RuntimeError | None = None
    for _ in range(max_attempts):
        port = _find_free_port()
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            _wait_for_server(port)
            return proc, port
        except RuntimeError as exc:
            proc.kill()
            stdout, stderr = proc.communicate(timeout=5)
            last_error = RuntimeError(
                f"{exc}\nstdout: {stdout.decode()}\nstderr: {stderr.decode()}"
            )
    raise RuntimeError(f"Server failed to start after {max_attempts} attempts: {last_error}")


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

    env = _build_e2e_env(
        db_path=db_path,
        thumbnail_dir=thumbnail_dir,
        download_dir=download_dir,
        log_dir=log_dir,
    )
    proc, port = _start_server(env=env, max_attempts=3)

    yield {
        "port": port,
        "base_url": f"http://127.0.0.1:{port}",
        "db_path": db_path,
        "thumbnail_dir": thumbnail_dir,
        "download_dir": download_dir,
        "process": proc,
    }

    proc.terminate()
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
        path = Path(str(item.fspath))
        if "tests" in path.parts and "e2e" in path.parts:
            item.add_marker(pytest.mark.e2e)
