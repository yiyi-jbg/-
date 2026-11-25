"""无人机推送接口、果树档案与自动关联、目录上传脚本的测试。"""

from __future__ import annotations

import argparse
import importlib.util
import socket
import threading
import time
from datetime import datetime
from pathlib import Path

import uvicorn

from app.config import Settings
from app.demo_data import DroneShot, make_scene, offset_position, save_drone_photo
from app.main import create_app

ROOT = Path(__file__).resolve().parents[2]
LAT, LON = 34.30215, 108.08571


def ingest(client, mission, *files, **fields):
    return client.post(
        "/api/v1/drone/ingest",
        data={"mission_code": mission, **fields},
        files=[("files", (name, data, "image/jpeg")) for name, data in files],
    )


def test_drone_ingest_creates_and_reuses_task(client, photo):
    first = ingest(client, "FLIGHT-0715", ("DJI_0001.JPG", photo("healthy")), orchard="示范果园", drone_model="M3E")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["task_created"] is True and body["accepted"] == 1

    second = ingest(client, "FLIGHT-0715", ("DJI_0001.JPG", photo("healthy")), ("DJI_0002.JPG", photo("chlorosis", seed=5)))
    body2 = second.json()
    assert body2["task_id"] == body["task_id"] and body2["task_created"] is False
    assert [i["status"] for i in body2["items"]] == ["duplicate", "accepted"]

    task = client.get(f"/api/v1/tasks/{body['task_id']}").json()
    assert task["source"] == "drone" and task["mission_code"] == "FLIGHT-0715"
    assert task["orchard"] == "示范果园" and task["drone_model"] == "M3E"
    assert task["flight_date"] == "2026-07-15"
    assert task["name"] == "无人机巡检 FLIGHT-0715"

    result = client.get("/api/v1/drone/missions/FLIGHT-0715").json()
    assert result["task"]["stats"]["analyzed_count"] == 2
    assert {i["health_level"] for i in result["images"]} >= {"healthy"}
    assert client.get("/api/v1/drone/missions/NOPE").status_code == 404


def test_tree_import_and_matching(client, photo):
    t1 = offset_position(LAT, LON, 0, 0)
    t2 = offset_position(LAT, LON, 6, 0)
    # Excel 中文版默认保存为 GBK 编码
    csv = f"编号,纬度,经度,品种,定植年份\nA-01,{t1[0]},{t1[1]},红富士,2016\nA-02,{t2[0]},{t2[1]},红富士,2017\n,,,\nA-03,abc,1\n"
    res = client.post("/api/v1/trees/import", files={"file": ("trees.csv", csv.encode("gbk"), "text/csv")}).json()
    assert res["created"] == 2 and res["updated"] == 0
    assert len(res["errors"]) == 1 and "第 5 行" in res["errors"][0]

    task = client.post("/api/v1/tasks", json={"name": "巡检"}).json()
    near_t2 = offset_position(LAT, LON, 6.8, 0.6)  # 距 A-02 约 1 米
    far = offset_position(LAT, LON, 30, 30)
    files = [
        ("files", ("p1.jpg", photo("healthy", latitude=t1[0], longitude=t1[1]), "image/jpeg")),
        ("files", ("p2.jpg", photo("chlorosis", latitude=near_t2[0], longitude=near_t2[1], seed=2), "image/jpeg")),
        ("files", ("p3.jpg", photo("healthy", latitude=far[0], longitude=far[1], seed=3), "image/jpeg")),
    ]
    client.post(f"/api/v1/tasks/{task['id']}/images", files=files)
    images = {i["original_filename"]: i for i in client.get("/api/v1/images", params={"task_id": task["id"]}).json()["items"]}
    assert images["p1.jpg"]["tree_code"] == "A-01"
    assert images["p2.jpg"]["tree_code"] == "A-02"
    assert images["p3.jpg"]["tree_id"] is None

    trees = {t["code"]: t for t in client.get("/api/v1/trees").json()["items"]}
    assert trees["A-02"]["image_count"] == 1 and trees["A-02"]["last_health_level"] in ("moderate", "severe")
    detail = client.get(f"/api/v1/trees/{trees['A-02']['id']}").json()
    assert len(detail["history"]) == 1 and detail["history"][0]["primary_issue"] == "叶片黄化"

    # 新登记一棵树后，之前未关联的照片自动关联
    new = client.post("/api/v1/trees", json={"code": "B-01", "latitude": far[0], "longitude": far[1]})
    assert new.status_code == 201 and new.json()["image_count"] == 1
    assert client.post("/api/v1/trees", json={"code": "B-01"}).status_code == 409

    # 删除果树后照片保留
    assert client.delete(f"/api/v1/trees/{new.json()['id']}").status_code == 204
    p3 = client.get(f"/api/v1/images/{images['p3.jpg']['id']}").json()
    assert p3["tree_id"] is None


def test_oblique_photo_matches_tree_under_camera_view(client, photo):
    tree = offset_position(LAT, LON, 20, 0)  # 飞机以东 20 米
    client.post("/api/v1/trees", json={"code": "T-1", "latitude": tree[0], "longitude": tree[1]})
    task = client.post("/api/v1/tasks", json={"name": "倾斜拍摄"}).json()
    data = photo("healthy", relative_altitude=20.0, gimbal_pitch=-45.0, gimbal_yaw=90.0)
    client.post(f"/api/v1/tasks/{task['id']}/images", files=[("files", ("o.jpg", data, "image/jpeg"))])
    img = client.get("/api/v1/images", params={"task_id": task["id"]}).json()["items"][0]
    assert img["tree_code"] == "T-1"
    detail = client.get(f"/api/v1/images/{img['id']}").json()
    assert detail["gsd_cm"] is None  # 倾斜照片不估算地面分辨率


def _load_uploader():
    spec = importlib.util.spec_from_file_location("drone_uploader", ROOT / "scripts" / "drone_uploader.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_folder_uploader_end_to_end(tmp_path):
    uploader_mod = _load_uploader()
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", process_inline=True, api_key="k123")
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        photos = tmp_path / "photos"
        for i, folder in enumerate(("DJI_202607150930_A区", "DJI_202607160930_B区")):
            (photos / folder).mkdir(parents=True)
            shot = DroneShot(latitude=LAT, longitude=LON, captured_at=datetime(2026, 7, 15 + i, 9, 30))
            for j in range(2):
                save_drone_photo(make_scene("healthy", size=(640, 480), seed=10 * i + j), photos / folder / f"DJI_{j}.JPG", shot)
        (photos / "readme.txt").write_text("not a photo")

        args = argparse.Namespace(
            server=f"http://127.0.0.1:{port}", dir=str(photos), api_key="k123", mission=None, orchard="示范果园",
            plot=None, fruit_type=None, drone_model=None, operator=None, flight_height=None, batch_size=3,
            settle=0, state=None,
        )
        up = uploader_mod.Uploader(args)
        assert up.run_once() == (4, 0)
        assert up.run_once() == (0, 0)  # 状态文件避免重复上传
        assert (photos / ".orchard_upload_state.json").exists()

        import json
        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/drone/missions/DJI_202607150930_A%E5%8C%BA") as r:
            mission = json.loads(r.read())
        assert mission["task"]["orchard"] == "示范果园"
        assert len(mission["images"]) == 2

        args.api_key = "wrong"
        (photos / "DJI_202607150930_A区" / "DJI_9.JPG").write_bytes((photos / "DJI_202607150930_A区" / "DJI_0.JPG").read_bytes())
        bad = uploader_mod.Uploader(args)
        try:
            bad.run_once()
            raise AssertionError("应当因密钥错误而失败")
        except uploader_mod.AuthError:
            pass
    finally:
        server.should_exit = True
        thread.join(timeout=10)
