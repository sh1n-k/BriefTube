from __future__ import annotations

import logging

from app.services.channel_resolver import ChannelResolverService
from app.services.takeout_parser import (
    ParsedTakeout,
    parse_bulk_text_inputs,
    parse_takeout_file_details,
    unique_preserve_order,
)

logger = logging.getLogger(__name__)
MAX_TAKEOUT_IMPORT_BYTES = 10 * 1024 * 1024


class TakeoutImportTooLargeError(ValueError):
    pass


def _dedupe_channels(channels: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for item in channels:
        channel_id = str(item.get("channel_id", "")).strip()
        channel_name = str(item.get("channel_name", "")).strip()
        channel_url = str(item.get("channel_url", "")).strip()
        if not channel_id or not channel_name:
            continue
        if channel_id in seen:
            continue
        seen.add(channel_id)
        out.append(
            {
                "channel_id": channel_id,
                "channel_name": channel_name,
                "channel_url": channel_url or f"https://www.youtube.com/channel/{channel_id}",
            }
        )
    return out


def collect_inputs_from_sources(bulk_text: str, takeout_data: ParsedTakeout) -> dict:
    text_entries = parse_bulk_text_inputs(bulk_text)
    merged_inputs: list[str] = [*takeout_data.inputs, *text_entries]
    merged_direct_channels: list[dict[str, str]] = [*takeout_data.direct_channels]

    return {
        "inputs": unique_preserve_order(merged_inputs),
        "direct_channels": _dedupe_channels(merged_direct_channels),
    }


async def resolve_bulk_inputs(
    inputs: list[str],
    resolver: ChannelResolverService,
    direct_channels: list[dict[str, str]] | None = None,
) -> dict:
    resolved: list[dict] = []
    needs_selection: list[dict] = []
    failed: list[dict] = []

    if direct_channels:
        for item in _dedupe_channels(direct_channels):
            resolved.append(
                {
                    "input": item["channel_name"],
                    "resolved": item,
                }
            )

    for raw in inputs:
        try:
            result = await resolver.resolve_input(raw)
        except Exception as exc:
            logger.warning(
                "event=channels.bulk_resolve_error input=%s error_type=%s",
                raw,
                exc.__class__.__name__,
            )
            failed.append(
                {
                    "input": raw,
                    "reason": f"resolver exception: {exc.__class__.__name__}",
                }
            )
            continue
        status = result.get("status")
        if status == "resolved":
            resolved.append(
                {
                    "input": raw,
                    "resolved": result["resolved"],
                }
            )
        elif status == "needs_selection":
            needs_selection.append(
                {
                    "input": raw,
                    "candidates": result.get("candidates", []),
                }
            )
        else:
            failed.append(
                {
                    "input": raw,
                    "reason": result.get("reason", "resolution failed"),
                }
            )

    return {
        "ok": True,
        "total_inputs": len(inputs) + len(direct_channels or []),
        "resolved": resolved,
        "needs_selection": needs_selection,
        "failed": failed,
    }


def parse_takeout_entries(filename: str, content: bytes) -> ParsedTakeout:
    if len(content) > MAX_TAKEOUT_IMPORT_BYTES:
        logger.warning(
            "event=channels.takeout_parse_rejected filename=%s size=%s limit=%s",
            filename,
            len(content),
            MAX_TAKEOUT_IMPORT_BYTES,
        )
        raise TakeoutImportTooLargeError("takeout file is too large")
    try:
        return parse_takeout_file_details(filename=filename, content=content)
    except Exception as exc:
        logger.warning(
            "event=channels.takeout_parse_failed filename=%s error_type=%s",
            filename,
            exc.__class__.__name__,
        )
        return ParsedTakeout(direct_channels=[], inputs=[])
