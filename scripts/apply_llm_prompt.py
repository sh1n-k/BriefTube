#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = REPO_ROOT / "prompts" / "llm_restructure" / "versions"
BACKUPS_DIR = REPO_ROOT / "prompts" / "llm_restructure" / "backups"
PROMPT_KEY = "llm_prompt_template"
_VERSION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _version_path(name: str) -> Path:
    raw = str(name or "").strip()
    if raw.endswith(".txt"):
        raw = raw[:-4]
    if not _VERSION_NAME_RE.fullmatch(raw):
        raise SystemExit(f"invalid version name: {name!r}")
    path = (VERSIONS_DIR / f"{raw}.txt").resolve()
    if not str(path).startswith(str(VERSIONS_DIR.resolve())):
        raise SystemExit(f"version path escapes versions dir: {name!r}")
    if not path.is_file():
        raise SystemExit(f"version not found: {path}")
    return path


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise SystemExit(f"db not found: {db_path}")
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'app_settings'"
    ).fetchone()
    if row is None:
        conn.close()
        raise SystemExit(f"app_settings table missing in {db_path}")
    return conn


def _get_prompt(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?",
        (PROMPT_KEY,),
    ).fetchone()
    if row is None:
        return ""
    return str(row[0] or "")


def _set_prompt(conn: sqlite3.Connection, value: str) -> None:
    conn.execute(
        """
        INSERT INTO app_settings(key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (PROMPT_KEY, value),
    )
    conn.commit()


def _backup_current(db_path: Path, prompt: str) -> Path:
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_db = re.sub(r"[^A-Za-z0-9._-]+", "_", db_path.name)
    out = BACKUPS_DIR / f"{stamp}_{safe_db}_llm_prompt_template.txt"
    out.write_text(prompt, encoding="utf-8")
    return out


def cmd_list(_: argparse.Namespace) -> int:
    if not VERSIONS_DIR.is_dir():
        print(f"versions dir missing: {VERSIONS_DIR}", file=sys.stderr)
        return 1
    files = sorted(VERSIONS_DIR.glob("*.txt"))
    if not files:
        print("(no versions)")
        return 0
    for path in files:
        text = _read_text(path)
        print(f"{path.stem}\t{len(text)} chars\t{path.relative_to(REPO_ROOT)}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    path = _version_path(args.version)
    sys.stdout.write(_read_text(path))
    if not _read_text(path).endswith("\n"):
        sys.stdout.write("\n")
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    conn = _connect(db_path)
    try:
        current = _get_prompt(conn)
    finally:
        conn.close()
    out = _backup_current(db_path, current)
    print(f"backed up {len(current)} chars -> {out.relative_to(REPO_ROOT)}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser().resolve()
    version_path = _version_path(args.version)
    next_prompt = _read_text(version_path)
    if "{transcript_text}" not in next_prompt:
        raise SystemExit("prompt must include {transcript_text}")

    conn = _connect(db_path)
    try:
        current = _get_prompt(conn)
        changed = current != next_prompt
        print(f"db: {db_path}")
        print(f"version: {version_path.stem}")
        print(f"current_chars: {len(current)}")
        print(f"next_chars: {len(next_prompt)}")
        print(f"changed: {changed}")
        if not args.write:
            print("dry-run only (pass --write to apply)")
            return 0
        if not changed:
            print("already applied; no write")
            return 0
        backup_path = _backup_current(db_path, current)
        _set_prompt(conn, next_prompt)
        print(f"backup: {backup_path.relative_to(REPO_ROOT)}")
        print("applied")
        return 0
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="List/show/apply versioned LLM restructure prompts to a local SQLite DB."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", help="List versioned prompt files")
    list_p.set_defaults(func=cmd_list)

    show_p = sub.add_parser("show", help="Print a versioned prompt")
    show_p.add_argument("--version", required=True)
    show_p.set_defaults(func=cmd_show)

    backup_p = sub.add_parser("backup", help="Backup current DB prompt only")
    backup_p.add_argument("--db", required=True, help="Path to SQLite DB")
    backup_p.set_defaults(func=cmd_backup)

    apply_p = sub.add_parser("apply", help="Apply a versioned prompt to DB")
    apply_p.add_argument("--db", required=True, help="Path to SQLite DB")
    apply_p.add_argument("--version", required=True, help="Version stem under versions/")
    apply_p.add_argument(
        "--write",
        action="store_true",
        help="Persist changes (default is dry-run)",
    )
    apply_p.set_defaults(func=cmd_apply)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
