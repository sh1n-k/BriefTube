from __future__ import annotations

from fastapi.testclient import TestClient

from app.repositories import categories as categories_repo
from app.repositories import channels as channels_repo
from tests.helpers.app_repo import call_repo

FRAGMENT_HEADERS = {"HX-Request": "true"}


def _add_channel(client: TestClient, channel_id: str, channel_name: str) -> dict:
    return call_repo(
        client,
        channels_repo.add_channel,
        channel_id=channel_id,
        channel_name=channel_name,
    )


def _create_category(client: TestClient, name: str) -> dict:
    return call_repo(client, categories_repo.create_category, name)


def _list_categories(client: TestClient) -> list[dict]:
    return call_repo(client, categories_repo.list_categories)


def test_default_category_created(client: TestClient) -> None:
    categories = _list_categories(client)
    assert len(categories) >= 1
    default = [c for c in categories if c["is_default"]]
    assert len(default) == 1
    assert default[0]["name"] == "미분류"


def test_create_category(client: TestClient) -> None:
    data = _create_category(client, "기술")
    assert data["name"] == "기술"
    assert data["processing_stage"] == "off"
    assert data["is_default"] == 0


def test_create_category_duplicate(client: TestClient) -> None:
    resp = client.post("/views/categories", data={"name": "뉴스", "status": "active"})
    assert resp.status_code == 200
    resp = client.post("/views/categories", data={"name": "뉴스", "status": "active"})
    assert resp.status_code == 400


def test_create_category_empty_name(client: TestClient) -> None:
    resp = client.post("/views/categories", data={"name": "", "status": "active"})
    assert resp.status_code == 400


def test_list_categories_with_channel_count(client: TestClient) -> None:
    cat_id = _create_category(client, "테크")["id"]
    _add_channel(client, "UC_tech1", "Tech Channel")
    client.post(
        f"/api/categories/{cat_id}/channels",
        json={"channel_ids": ["UC_tech1"]},
    )
    cats = _list_categories(client)
    tech_cat = next(c for c in cats if c["id"] == cat_id)
    assert tech_cat["channel_count"] == 1


def test_rename_category(client: TestClient) -> None:
    cat_id = _create_category(client, "원래이름")["id"]
    rename_resp = client.put(
        f"/api/categories/{cat_id}",
        json={"name": "새이름"},
    )
    assert rename_resp.status_code == 200
    assert rename_resp.json()["renamed"] is True


def test_category_processing_stage_contract(client: TestClient) -> None:
    cat_id = _create_category(client, "스테이지테스트")["id"]
    categories = _list_categories(client)
    created = next(c for c in categories if c["id"] == cat_id)
    assert created["processing_stage"] in {"off", "transcript_only", "full"}
    assert created["processing_stage"] == "off"
    update_resp = client.put(
        f"/api/categories/{cat_id}",
        json={"processing_stage": "full"},
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["processing_stage"] == "full"


def test_category_processing_stage_rejects_invalid_value(client: TestClient) -> None:
    cat_id = _create_category(client, "스테이지오류")["id"]
    update_resp = client.put(
        f"/api/categories/{cat_id}",
        json={"processing_stage": "invalid-stage"},
    )
    assert update_resp.status_code == 400


def test_delete_category(client: TestClient) -> None:
    cat_id = _create_category(client, "삭제대상")["id"]
    _add_channel(client, "UC_del1", "Delete Channel")
    client.post(
        f"/api/categories/{cat_id}/channels",
        json={"channel_ids": ["UC_del1"]},
    )
    del_resp = client.delete(f"/views/categories/{cat_id}?status=active")
    assert del_resp.status_code == 200
    assert all(c["id"] != cat_id for c in _list_categories(client))
    channel = call_repo(client, channels_repo.get_channel_by_id, "UC_del1")
    default_id = call_repo(client, categories_repo.get_default_category_id)
    assert channel["category_id"] == default_id


def test_delete_default_category_fails(client: TestClient) -> None:
    cats = _list_categories(client)
    default_cat = next(c for c in cats if c["is_default"])
    resp = client.delete(f"/views/categories/{default_cat['id']}?status=active")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "cannot delete default category"


def test_reorder_categories(client: TestClient) -> None:
    _create_category(client, "순서A")
    _create_category(client, "순서B")
    cats = _list_categories(client)
    ids = [c["id"] for c in cats]
    reversed_ids = list(reversed(ids))
    resp = client.put(
        "/api/categories/reorder",
        json={"ordered_ids": reversed_ids},
    )
    assert resp.status_code == 200
    reordered = _list_categories(client)
    reordered_ids = [c["id"] for c in reordered]
    assert reordered_ids == reversed_ids


def test_reorder_categories_rejects_invalid_json_shape(client: TestClient) -> None:
    for payload in ([], {"ordered_ids": "bad"}, {"ordered_ids": ["bad"]}, {"ordered_ids": [None]}):
        response = client.put("/api/categories/reorder", json=payload)
        assert response.status_code == 400


def test_move_channels_to_category(client: TestClient) -> None:
    cat_id = _create_category(client, "이동대상")["id"]
    _add_channel(client, "UC_mv1", "Move Ch 1")
    _add_channel(client, "UC_mv2", "Move Ch 2")
    move_resp = client.post(
        f"/api/categories/{cat_id}/channels",
        json={"channel_ids": ["UC_mv1", "UC_mv2"]},
    )
    assert move_resp.status_code == 200
    assert move_resp.json()["moved"] == 2


def test_move_channels_to_category_rejects_invalid_json_shape(client: TestClient) -> None:
    category_id = _create_category(client, "이동형식오류")["id"]
    for payload in ([], {"channel_ids": "bad"}):
        response = client.post(f"/api/categories/{category_id}/channels", json=payload)
        assert response.status_code == 400


def test_channel_management_page_with_category_filter(client: TestClient) -> None:
    cats = _list_categories(client)
    default_id = next(c for c in cats if c["is_default"])["id"]
    resp = client.get(f"/channels?category_id={default_id}")
    assert resp.status_code == 200


def test_category_sidebar_fragment_contract(client: TestClient) -> None:
    response = client.get("/views/category-sidebar?status=active", headers=FRAGMENT_HEADERS)
    assert response.status_code == 200
    html = response.text
    assert "<html" not in html.lower()
    assert "<body" not in html.lower()
    assert 'id="category-sidebar"' in html
    assert 'hx-target="#channel-list-wrap"' in html


def test_channel_management_page_renders_category_rename_controls(client: TestClient) -> None:
    _create_category(client, "이름변경대상")

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
    assert "<html" not in html.lower()
    assert "<body" not in html.lower()
    assert 'id="channel-list-wrap" hx-swap-oob="true"' in html
    assert 'id="category-sidebar"' in html
    assert 'hx-swap-oob="true"' in html
    assert "data-channel-move-target" in html
    assert "즉시반영" in html


def test_delete_category_fragment_refreshes_channel_list_oob_and_clears_selected_deleted_category(
    client: TestClient,
) -> None:
    category_id = int(_create_category(client, "삭제즉시반영")["id"])

    response = client.delete(
        f"/views/categories/{category_id}?status=active&category_id={category_id}",
    )
    assert response.status_code == 200
    html = response.text
    assert 'id="channel-list-wrap" hx-swap-oob="true"' in html
    assert "data-channel-move-target" in html
    assert f"category_id={category_id}" not in html
