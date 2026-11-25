"""接口测试：任务、照片上传与分析、统计、导出、密钥校验。"""

from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def create_task(client: TestClient, **body) -> dict:
    res = client.post("/api/v1/tasks", json={"name": "A区巡检", "orchard": "示范果园", **body})
    assert res.status_code == 201, res.text
    return res.json()


def upload(client: TestClient, task_id: int, *files: tuple[str, bytes], headers=None):
    return client.post(
        f"/api/v1/tasks/{task_id}/images",
        files=[("files", (name, data, "image/jpeg")) for name, data in files],
        headers=headers or {},
    )


def test_upload_and_analyze(client, photo, settings):
    task = create_task(client, fruit_type="苹果")
    res = upload(client, task["id"], ("ok.jpg", photo("healthy")), ("sick.jpg", photo("chlorosis", seed=2)))
    assert res.status_code == 200
    body = res.json()
    assert body["accepted"] == 2 and body["rejected"] == 0

    page = client.get("/api/v1/images", params={"task_id": task["id"], "sort": "score_asc"}).json()
    assert page["total"] == 2
    worst, best = page["items"]
    assert worst["original_filename"] == "sick.jpg"
    assert worst["status"] == "done" and worst["health_level"] in ("moderate", "severe")
    assert worst["primary_issue"] == "叶片黄化"
    assert best["health_level"] == "healthy" and best["health_level_label"] == "健康"
    assert best["growth_stage_label"] == "果实发育期"

    detail = client.get(f"/api/v1/images/{worst['id']}").json()
    assert detail["latitude"] is not None and detail["altitude_rel"] == 30.0
    assert detail["gsd_cm"] > 0
    assert detail["result"]["health"]["issues"][0]["code"] == "chlorosis"
    assert detail["result"]["growth"]["canopy_area_m2"] > 0
    assert detail["metadata"]["camera_make"] == "DJI"

    for kind in ("file", "thumbnail", "overlay"):
        r = client.get(f"/api/v1/images/{worst['id']}/{kind}")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"

    stats = client.get(f"/api/v1/tasks/{task['id']}").json()["stats"]
    assert stats["image_count"] == 2 and stats["analyzed_count"] == 2 and stats["abnormal_count"] == 1


def test_duplicate_and_invalid_uploads(client, photo):
    task = create_task(client)
    data = photo("healthy")
    assert upload(client, task["id"], ("a.jpg", data)).json()["accepted"] == 1
    res = upload(client, task["id"], ("a-again.jpg", data), ("notes.txt", b"hello"), ("gray.png", _gray_png())).json()
    statuses = [i["status"] for i in res["items"]]
    assert statuses == ["duplicate", "rejected", "rejected"]
    assert "单波段" in res["items"][2]["error"]
    # 同一照片可以进入另一个任务
    other = create_task(client, name="复飞")
    assert upload(client, other["id"], ("a.jpg", data)).json()["accepted"] == 1


def _gray_png() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("L", (64, 64), 100).save(buf, "PNG")
    return buf.getvalue()


def test_upload_size_limit(tmp_path, photo):
    settings = Settings(_env_file=None, data_dir=tmp_path / "d", process_inline=True, max_upload_mb=1)
    with TestClient(create_app(settings)) as client:
        task = create_task(client)
        big = photo("healthy", size=(2400, 1800)) + b"\0" * (1024 * 1024)
        item = upload(client, task["id"], ("big.jpg", big)).json()["items"][0]
        assert item["status"] == "rejected" and "1MB" in item["error"]
        assert not list((tmp_path / "d" / "uploads").rglob("*.part"))


def test_overview_map_and_export(client, photo):
    task = create_task(client)
    upload(client, task["id"], ("1.jpg", photo("healthy")), ("2.jpg", photo("necrosis", seed=4, longitude=108.0858)))
    overview = client.get("/api/v1/stats/overview").json()
    assert overview["image_count"] == 2 and overview["analyzed_count"] == 2
    assert {c["code"] for c in overview["health_levels"]} >= {"healthy"}
    assert overview["top_issues"][0]["label"] == "叶片褐变/坏死"
    assert overview["trend"][0]["date"] == "2026-07-15"
    assert len(overview["recent_alerts"]) == 1

    points = client.get("/api/v1/stats/map", params={"task_id": task["id"]}).json()["images"]
    assert len(points) == 2

    csv = client.get(f"/api/v1/tasks/{task['id']}/export.csv")
    assert csv.status_code == 200
    assert csv.content.startswith(b"\xef\xbb\xbf")  # Excel 识别 UTF-8 所需的 BOM
    text = csv.content.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("照片ID,文件名,拍摄时间")
    assert "叶片褐变/坏死" in text


def test_reanalyze_and_delete(client, photo, settings):
    task = create_task(client)
    image_id = upload(client, task["id"], ("1.jpg", photo("healthy"))).json()["items"][0]["image_id"]
    res = client.post(f"/api/v1/images/{image_id}/reanalyze")
    assert res.status_code == 200 and res.json()["status"] == "done"
    assert client.post(f"/api/v1/tasks/{task['id']}/reanalyze").json() == {"queued": 1}

    files = [p for p in settings.data_dir.rglob("*.jpg")]
    assert len(files) == 3  # 原图、缩略图、标注图
    assert client.delete(f"/api/v1/images/{image_id}").status_code == 204
    assert client.get(f"/api/v1/images/{image_id}").status_code == 404
    assert not list(settings.data_dir.rglob("*.jpg"))

    upload(client, task["id"], ("2.jpg", photo("healthy", seed=9)))
    assert client.delete(f"/api/v1/tasks/{task['id']}").status_code == 204
    assert client.get(f"/api/v1/tasks/{task['id']}").status_code == 404
    assert not (settings.data_dir / "uploads" / str(task["id"])).exists()


def test_task_crud_and_search(client):
    a = create_task(client, name="七月 A 区", mission_code="M-001")
    create_task(client, name="八月 B 区")
    assert client.post("/api/v1/tasks", json={"name": "重复", "mission_code": "M-001"}).status_code == 409
    assert client.post("/api/v1/tasks", json={"name": ""}).status_code == 422
    found = client.get("/api/v1/tasks", params={"q": "A 区"}).json()
    assert [t["id"] for t in found["items"]] == [a["id"]]
    res = client.patch(f"/api/v1/tasks/{a['id']}", json={"plot": "A1", "flight_height_m": 25})
    assert res.json()["plot"] == "A1" and res.json()["name"] == "七月 A 区"


def test_api_key_protects_writes(tmp_path, photo):
    settings = Settings(_env_file=None, data_dir=tmp_path / "d", process_inline=True, api_key="secret-key")
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/v1/tasks", json={"name": "x"}).status_code == 401
        assert client.post("/api/v1/tasks", json={"name": "x"}, headers={"X-API-Key": "wrong"}).status_code == 401
        ok = client.post("/api/v1/tasks", json={"name": "x"}, headers={"X-API-Key": "secret-key"})
        assert ok.status_code == 201
        assert upload(client, ok.json()["id"], ("a.jpg", photo())).status_code == 401
        # 查询接口保持开放
        assert client.get("/api/v1/tasks").status_code == 200
        assert client.get("/api/v1/system/info").json()["api_key_required"] is True


def test_analyze_endpoint(client, photo):
    res = client.post(
        "/api/v1/analyze", params={"include_overlay": "true"},
        files={"file": ("x.jpg", photo("necrosis", captured_at=datetime(2026, 6, 1, 10)), "image/jpeg")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["metadata"]["latitude"] is not None
    assert body["result"]["health"]["issues"][0]["code"] == "necrosis"
    assert body["overlay_base64"]
    assert client.get("/api/v1/tasks").json()["total"] == 0  # 不落库
    bad = client.post("/api/v1/analyze", files={"file": ("x.jpg", b"not an image", "image/jpeg")})
    assert bad.status_code == 400


def test_system_info_and_frontend(client):
    info = client.get("/api/v1/system/info").json()
    assert info["enums"]["health_levels"]["healthy"] == "健康"
    assert info["model_loaded"] is False
    assert client.get("/healthz").json() == {"status": "ok"}
    index = client.get("/")
    assert index.status_code == 200 and "智慧果园" in index.text
    assert client.get("/js/app.js").headers["content-type"].startswith("text/javascript")
