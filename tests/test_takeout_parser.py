from __future__ import annotations

from app.services.bulk_channels import collect_inputs_from_sources, parse_takeout_entries
from app.services.takeout_parser import (
    parse_bulk_text_inputs,
    parse_takeout_file,
    parse_takeout_file_details,
)


def test_parse_bulk_text_inputs_supports_delimiters_and_dedupe() -> None:
    raw = "@channelA, @channelA\nUC_x5XG1OV2P6uZZ5FSM9Ttw; https://www.youtube.com/@MKBHD"
    items = parse_bulk_text_inputs(raw)
    assert items == [
        "@channelA",
        "UC_x5XG1OV2P6uZZ5FSM9Ttw",
        "https://www.youtube.com/@MKBHD",
    ]


def test_parse_takeout_json_extracts_channel_tokens() -> None:
    content = b'{"subscriptions":[{"channelId":"UCjson001","title":"JSON Channel","url":"https://www.youtube.com/@json"}]}'
    items = parse_takeout_file("subscriptions.json", content)
    assert "UCjson001" in items
    assert "https://www.youtube.com/@json" in items


def test_parse_takeout_csv_extracts_channel_fields() -> None:
    content = (
        b"channel_name,channel_url,channel_id\nCSV Channel,https://www.youtube.com/@csv,UCcsv001\n"
    )
    items = parse_takeout_file("subscriptions.csv", content)
    assert "CSV Channel" in items
    assert "https://www.youtube.com/@csv" in items
    assert "UCcsv001" in items


def test_parse_google_subscriptions_csv_direct_channels() -> None:
    content = (
        b"Channel Id,Channel Url,Channel Title\n"
        b"UC0byV7SMA-MjzByM5fZR1EA,http://www.youtube.com/channel/UC0byV7SMA-MjzByM5fZR1EA,\xeb\xb2\x94\xec\xa3\x84\xec\x8b\xac\xeb\xa6\xac \xec\x97\xb0\xea\xb5\xac\xec\x86\x8c\n"
    )
    parsed = parse_takeout_file_details("subscriptions.csv", content)
    assert len(parsed.direct_channels) == 1
    assert parsed.direct_channels[0]["channel_id"] == "UC0byV7SMA-MjzByM5fZR1EA"
    assert parsed.direct_channels[0]["channel_name"] == "범죄심리 연구소"


def test_collect_inputs_merges_takeout_and_text_inputs() -> None:
    takeout_data = parse_takeout_entries(
        "subscriptions.csv",
        (
            "Channel Id,Channel Url,Channel Title\n"
            "UC0oRRcVleNBmELYTgzwoBpg,http://www.youtube.com/channel/UC0oRRcVleNBmELYTgzwoBpg,유브이 방 - UV BANG\n"
        ).encode(),
    )
    collected = collect_inputs_from_sources(
        bulk_text="@GoogleDevelopers\nhttps://www.youtube.com/@MKBHD",
        takeout_data=takeout_data,
    )
    assert len(collected["direct_channels"]) == 1
    assert collected["direct_channels"][0]["channel_id"] == "UC0oRRcVleNBmELYTgzwoBpg"
    assert "@GoogleDevelopers" in collected["inputs"]
    assert "https://www.youtube.com/@MKBHD" in collected["inputs"]


def test_parse_takeout_json_supports_utf8_bom() -> None:
    content = b'\xef\xbb\xbf{"subscriptions":[{"channelId":"UCbom001","url":"https://www.youtube.com/@bom"}]}'
    items = parse_takeout_file("subscriptions.json", content)
    assert "UCbom001" in items
    assert "https://www.youtube.com/@bom" in items


def test_parse_takeout_csv_supports_semicolon_delimiter() -> None:
    content = (
        b"Channel Id;Channel Url;Channel Title\n"
        b"UC0byV7SMA-MjzByM5fZR1EA;https://www.youtube.com/channel/UC0byV7SMA-MjzByM5fZR1EA;Semi Channel\n"
    )
    parsed = parse_takeout_file_details("subscriptions.csv", content)
    assert len(parsed.direct_channels) == 1
    assert parsed.direct_channels[0]["channel_name"] == "Semi Channel"


def test_parse_takeout_csv_supports_korean_headers_and_cp949() -> None:
    content = (
        "채널 아이디,채널 이름,채널 링크\n"
        "UC0oRRcVleNBmELYTgzwoBpg,테스트 채널,https://www.youtube.com/channel/UC0oRRcVleNBmELYTgzwoBpg\n"
    ).encode("cp949")
    parsed = parse_takeout_file_details("subscriptions.csv", content)
    assert len(parsed.direct_channels) == 1
    assert parsed.direct_channels[0]["channel_name"] == "테스트 채널"


def test_parse_takeout_csv_falls_back_to_channel_id_when_name_missing() -> None:
    content = (
        b"Channel Id,Channel Url,Channel Title\n"
        b"UC0byV7SMA-MjzByM5fZR1EA,https://www.youtube.com/channel/UC0byV7SMA-MjzByM5fZR1EA,\n"
    )
    parsed = parse_takeout_file_details("subscriptions.csv", content)
    assert len(parsed.direct_channels) == 1
    assert parsed.direct_channels[0]["channel_name"] == "UC0byV7SMA-MjzByM5fZR1EA"
