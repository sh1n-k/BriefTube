from __future__ import annotations

import sys

import uvicorn


def main() -> None:
    host = "0.0.0.0"
    port = 8000

    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--host" and i < len(sys.argv) - 1:
            host = sys.argv[i + 1]
        elif arg == "--port" and i < len(sys.argv) - 1:
            port = int(sys.argv[i + 1])

    uvicorn.run("app.main:app", host=host, port=port)


if __name__ == "__main__":
    main()
