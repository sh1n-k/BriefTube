from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from app.remote_sync_metadata import SYNC_NOW_SQL
from app.services.llm_payload import (
    article_body_format_invalid,
    article_fact_box_format_invalid,
    looks_like_over_escaped_markdown,
    normalize_article_text,
    normalize_fact_box_text,
)


def _iter_candidates(conn: sqlite3.Connection, video_ids: list[str] | None) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    if video_ids:
        placeholders = ",".join("?" for _ in video_ids)
        sql = f"""
            SELECT video_id, body, fact_box
            FROM articles
            WHERE deleted_at IS NULL
              AND video_id IN ({placeholders})
        """
        return list(conn.execute(sql, tuple(video_ids)))
    return list(
        conn.execute(
            """
            SELECT video_id, body, fact_box
            FROM articles
            WHERE deleted_at IS NULL
            """
        )
    )


def _needs_fix(body: str, fact_box: str) -> bool:
    return looks_like_over_escaped_markdown(body) or looks_like_over_escaped_markdown(fact_box)


def _origin_device_id(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = 'remote_sync_device_id'"
    ).fetchone()
    if row is None:
        return ""
    return str(row[0] or "")


def backfill(db_path: Path, *, dry_run: bool, video_ids: list[str] | None) -> int:
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        rows = _iter_candidates(conn, video_ids)
        origin = _origin_device_id(conn)
        updated = 0
        skipped_invalid = 0
        skipped_cas = 0
        for row in rows:
            video_id = str(row["video_id"])
            body = str(row["body"] or "")
            fact_box = str(row["fact_box"] or "")
            if not _needs_fix(body, fact_box):
                continue

            new_body = normalize_article_text(body)
            new_fact_box = normalize_fact_box_text(fact_box)
            if article_body_format_invalid(new_body) or article_fact_box_format_invalid(
                new_fact_box
            ):
                print(
                    f"skip_invalid video_id={video_id} "
                    f"body_literal_n={body.count(chr(92) + 'n')} "
                    f"fact_literal_n={fact_box.count(chr(92) + 'n')}",
                    file=sys.stderr,
                )
                skipped_invalid += 1
                continue

            if new_body == body and new_fact_box == fact_box:
                continue

            print(
                f"{'dry_run ' if dry_run else ''}"
                f"fix video_id={video_id} "
                f"body {body.count(chr(92) + 'n')}->real_nl={new_body.count(chr(10))} "
                f"fact_box changed={new_fact_box != fact_box}"
            )
            if dry_run:
                updated += 1
                continue

            cursor = conn.execute(
                f"""
                UPDATE articles
                SET body = ?,
                    fact_box = ?,
                    updated_at = {SYNC_NOW_SQL},
                    sync_dirty = 1,
                    origin_device_id = COALESCE(NULLIF(?, ''), origin_device_id)
                WHERE video_id = ?
                  AND deleted_at IS NULL
                  AND body = ?
                  AND IFNULL(fact_box, '') = IFNULL(?, '')
                """,
                (new_body, new_fact_box, origin, video_id, body, fact_box),
            )
            if int(cursor.rowcount or 0) == 0:
                print(f"skip_cas video_id={video_id}", file=sys.stderr)
                skipped_cas += 1
                continue
            conn.commit()
            updated += 1

        print(
            f"updated={updated} skipped_invalid={skipped_invalid} "
            f"skipped_cas={skipped_cas} scanned={len(rows)}"
        )
        return updated
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize over-escaped \\\\n sequences in stored article body/markdown fact_box. "
            "Prefer maintenance window while writers are idle."
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        required=True,
        help="SQLite database path (e.g. data.prod.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report candidates without writing",
    )
    parser.add_argument(
        "--video-id",
        action="append",
        dest="video_ids",
        default=None,
        help="Limit to one or more video_id values (repeatable)",
    )
    args = parser.parse_args()
    db_path = args.db.expanduser().resolve()
    if not db_path.is_file():
        print(f"database not found: {db_path}", file=sys.stderr)
        return 2
    backfill(db_path, dry_run=bool(args.dry_run), video_ids=args.video_ids)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
