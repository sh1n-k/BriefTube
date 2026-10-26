from __future__ import annotations

import argparse
import sys
from pathlib import Path

from granian import Granian
from granian.constants import Interfaces, Loops
from watchfiles import PythonFilter

from app.config import load_config

APP_DIR = Path(__file__).resolve().parent


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="brieftube")
    parser.add_argument("--host", help="Override server_host from the selected YAML config.")
    parser.add_argument(
        "--port",
        type=int,
        help="Override server_port from the selected YAML config.",
    )
    reload_group = parser.add_mutually_exclusive_group()
    reload_group.add_argument(
        "--reload",
        action="store_true",
        default=None,
        help="Enable server reload regardless of YAML config.",
    )
    reload_group.add_argument(
        "--no-reload",
        action="store_false",
        dest="reload",
        help="Disable server reload regardless of YAML config.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args(sys.argv[1:])
    config = load_config()

    host = str(args.host or config.server_host)
    port = int(args.port if args.port is not None else config.server_port)
    reload = bool(config.server_reload if args.reload is None else args.reload)

    # Background workers start in the ASGI lifespan, so exactly one worker process may run.
    # Reload watches only app/*.py; the default filter would restart on DB and log writes.
    Granian(
        "app.main:app",
        address=host,
        port=port,
        interface=Interfaces.ASGI,
        workers=1,
        loop=Loops.asyncio,
        reload=reload,
        reload_paths=[APP_DIR],
        reload_filter=PythonFilter,
    ).serve()


if __name__ == "__main__":
    main()
