from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _add_channel(client: TestClient, channel_id: str, channel_name: str) -> dict:
    resp = client.post(
        "/api/channels",
        json={"channel_id": channel_id, "channel_name": channel_name},
    )
    assert resp.status_code == 200
    return resp.json()


def test_default_category_created(client: TestClient) -> None:
    resp = client.get("/api/categories")
    assert resp.status_code == 200
    categories = resp.json()
    assert len(categories) >= 1
    default = [c for c in categories if c["is_default"]]
    assert len(default) == 1
    assert default[0]["name"] == "미분류"


def test_create_category(client: TestClient) -> None:
    resp = client.post("/api/categories", json={"name": "기술"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "기술"
    assert data["processing_stage"] == "off"
    assert data["is_default"] == 0


def test_create_category_duplicate(client: TestClient) -> None:
    client.post("/api/categories", json={"name": "뉴스"})
    resp = client.post("/api/categories", json={"name": "뉴스"})
    assert resp.status_code == 400


def test_create_category_empty_name(client: TestClient) -> None:
    resp = client.post("/api/categories", json={"name": ""})
    assert resp.status_code == 400


def test_list_categories_with_channel_count(client: TestClient) -> None:
    resp = client.post("/api/categories", json={"name": "테크"})
    cat_id = resp.json()["id"]
    _add_channel(client, "UC_tech1", "Tech Channel")
    client.post(
        f"/api/categories/{cat_id}/channels",
        json={"channel_ids": ["UC_tech1"]},
    )
    cats = client.get("/api/categories").json()
    tech_cat = [c for c in cats if c["id"] == cat_id][0]
    assert tech_cat["channel_count"] == 1


def test_rename_category(client: TestClient) -> None:
    resp = client.post("/api/categories", json={"name": "원래이름"})
    cat_id = resp.json()["id"]
    rename_resp = client.put(
        f"/api/categories/{cat_id}",
        json={"name": "새이름"},
    )
    assert rename_resp.status_code == 200
    assert rename_resp.json()["renamed"] is True


def test_category_processing_stage_contract(client: TestClient) -> None:
    resp = client.post("/api/categories", json={"name": "스테이지테스트"})
    cat_id = resp.json()["id"]
    categories = client.get("/api/categories").json()
    created = [c for c in categories if c["id"] == cat_id][0]
    assert created["processing_stage"] in {"off", "transcript_only", "full"}
    assert created["processing_stage"] == "off"
    update_resp = client.put(
        f"/api/categories/{cat_id}",
        json={"processing_stage": "full"},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["processing_stage"] == "full"


def test_category_processing_stage_rejects_invalid_value(client: TestClient) -> None:
    resp = client.post("/api/categories", json={"name": "스테이지오류"})
    cat_id = resp.json()["id"]
    update_resp = client.put(
        f"/api/categories/{cat_id}",
        json={"processing_stage": "invalid-stage"},
    )
    assert update_resp.status_code == 400


def test_delete_category(client: TestClient) -> None:
    resp = client.post("/api/categories", json={"name": "삭제대상"})
    cat_id = resp.json()["id"]
    _add_channel(client, "UC_del1", "Delete Channel")
    client.post(
        f"/api/categories/{cat_id}/channels",
        json={"channel_ids": ["UC_del1"]},
    )
    del_resp = client.delete(f"/api/categories/{cat_id}")
    assert del_resp.status_code == 200
    data = del_resp.json()
    assert data["deleted"] == 1
    assert data["channels_moved"] == 1


def test_delete_default_category_fails(client: TestClient) -> None:
    cats = client.get("/api/categories").json()
    default_cat = [c for c in cats if c["is_default"]][0]
    resp = client.delete(f"/api/categories/{default_cat['id']}")
    assert resp.status_code == 400


def test_reorder_categories(client: TestClient) -> None:
    client.post("/api/categories", json={"name": "순서A"})
    client.post("/api/categories", json={"name": "순서B"})
    cats = client.get("/api/categories").json()
    ids = [c["id"] for c in cats]
    reversed_ids = list(reversed(ids))
    resp = client.put(
        "/api/categories/reorder",
        json={"ordered_ids": reversed_ids},
    )
    assert resp.status_code == 200
    reordered = client.get("/api/categories").json()
    reordered_ids = [c["id"] for c in reordered]
    assert reordered_ids == reversed_ids


def test_move_channels_to_category(client: TestClient) -> None:
    resp = client.post("/api/categories", json={"name": "이동대상"})
    cat_id = resp.json()["id"]
    _add_channel(client, "UC_mv1", "Move Ch 1")
    _add_channel(client, "UC_mv2", "Move Ch 2")
    move_resp = client.post(
        f"/api/categories/{cat_id}/channels",
        json={"channel_ids": ["UC_mv1", "UC_mv2"]},
    )
    assert move_resp.status_code == 200
    assert move_resp.json()["moved"] == 2


def test_video_list_category_filter(client: TestClient) -> None:
    resp = client.post("/api/categories", json={"name": "필터카테고리"})
    cat_id = resp.json()["id"]
    _add_channel(client, "UC_flt1", "Filter Ch")
    client.post(
        f"/api/categories/{cat_id}/channels",
        json={"channel_ids": ["UC_flt1"]},
    )
    resp = client.get(f"/?category_id={cat_id}")
    assert resp.status_code == 200


def test_channel_management_page_renders(client: TestClient) -> None:
    resp = client.get("/channels")
    assert resp.status_code == 200
    assert "category-sidebar" in resp.text


def test_channel_management_page_with_category_filter(client: TestClient) -> None:
    cats = client.get("/api/categories").json()
    default_id = [c for c in cats if c["is_default"]][0]["id"]
    resp = client.get(f"/channels?category_id={default_id}")
    assert resp.status_code == 200


def test_channel_management_page_renders_category_rename_controls(client: TestClient) -> None:
    created = client.post("/api/categories", json={"name": "이름변경대상"})
    assert created.status_code == 200

    response = client.get("/channels")
    assert response.status_code == 200
    html = response.text
    assert "data-category-rename-trigger" in html
    assert 'data-rename-title="' in html
    assert 'data-rename-success-toast="' in html
    assert "이름변경대상" in html


def test_create_category_fragment_refreshes_channel_list_oob(client: TestClient) -> None:
    response = client.post(
        "/views/categories",
        data={"name": "즉시반영", "status": "active"},
    )
    assert response.status_code == 200
    html = response.text
    assert 'id="channel-list-wrap" hx-swap-oob="true"' in html
    assert "data-channel-move-target" in html
    assert "즉시반영" in html


def test_delete_category_fragment_refreshes_channel_list_oob_and_clears_selected_deleted_category(
    client: TestClient,
) -> None:
    created = client.post("/api/categories", json={"name": "삭제즉시반영"})
    assert created.status_code == 200
    category_id = int(created.json()["id"])

    response = client.delete(
        f"/views/categories/{category_id}?status=active&category_id={category_id}",
    )
    assert response.status_code == 200
    html = response.text
    assert 'id="channel-list-wrap" hx-swap-oob="true"' in html
    assert "data-channel-move-target" in html
    assert f"category_id={category_id}" not in html
