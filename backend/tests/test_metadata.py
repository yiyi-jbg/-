"""无人机元数据解析与地理计算测试。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from app.demo_data import DroneShot, make_scene, save_drone_photo
from app.drone.geo import distance_m, ground_point, nearest, offset
from app.drone.metadata import compute_gsd_cm, parse_xmp, read_metadata


def test_read_dji_metadata(tmp_path: Path):
    shot = DroneShot(
        latitude=34.30215, longitude=108.08571, captured_at=datetime(2026, 7, 15, 9, 30, 5),
        relative_altitude=30.2, absolute_altitude=452.1, gimbal_pitch=-90, gimbal_yaw=12.5, flight_yaw=11.0,
    )
    path = save_drone_photo(make_scene("healthy", size=(800, 600)), tmp_path / "dji.jpg", shot)
    meta = read_metadata(path)
    assert meta.width == 800 and meta.height == 600
    assert meta.captured_at == datetime(2026, 7, 15, 9, 30, 5)
    assert meta.latitude == pytest.approx(34.30215, abs=1e-6)
    assert meta.longitude == pytest.approx(108.08571, abs=1e-6)
    assert meta.altitude_abs == pytest.approx(452.1, abs=0.01)
    assert meta.altitude_rel == pytest.approx(30.2)
    assert meta.gimbal_pitch == -90 and meta.gimbal_yaw == 12.5 and meta.flight_yaw == 11.0
    assert meta.camera_make == "DJI" and meta.camera_model == "FC6310"
    assert meta.focal_length_35mm == 24
    assert meta.raw["xmp"]["RtkFlag"] == "50"


def test_southern_western_coordinates(tmp_path: Path):
    shot = DroneShot(latitude=-33.45, longitude=-70.66, captured_at=datetime(2026, 1, 5, 8, 0))
    meta = read_metadata(save_drone_photo(make_scene("healthy", size=(640, 480)), tmp_path / "s.jpg", shot))
    assert meta.latitude == pytest.approx(-33.45, abs=1e-6)
    assert meta.longitude == pytest.approx(-70.66, abs=1e-6)


def test_photo_without_metadata(tmp_path: Path):
    path = tmp_path / "plain.png"
    Image.new("RGB", (320, 240), (40, 120, 40)).save(path)
    meta = read_metadata(path)
    assert meta.format == "PNG"
    assert not meta.has_gps
    assert meta.captured_at is None


def test_parse_xmp_element_form_and_typo():
    xmp = """<rdf:Description><drone-dji:RelativeAltitude>+45.30</drone-dji:RelativeAltitude>
             <drone-dji:GpsLongtitude>113.25</drone-dji:GpsLongtitude></rdf:Description>"""
    fields = parse_xmp(xmp)
    assert fields == {"RelativeAltitude": "+45.30", "GpsLongtitude": "113.25"}


def test_gsd_matches_dji_spec():
    # Phantom 4 Pro：5472×3648，35mm 等效焦距 24mm，30 米高度约 0.82 厘米/像素
    assert compute_gsd_cm(30, 24, 5472, 3648, -90) == pytest.approx(0.822, abs=0.005)
    assert compute_gsd_cm(30, 24, 5472, 3648, None) == pytest.approx(0.822, abs=0.005)
    assert compute_gsd_cm(30, 24, 5472, 3648, -45) is None  # 倾斜拍摄
    assert compute_gsd_cm(None, 24, 5472, 3648, -90) is None
    assert compute_gsd_cm(30, None, 5472, 3648, -90) is None


def test_ground_point_projection():
    lat, lon = 34.3, 108.08
    assert ground_point(lat, lon, 30, -90, 0) == (lat, lon)
    # 俯仰 -45°、朝东：画面中心在正东约 30 米
    glat, glon = ground_point(lat, lon, 30, -45, 90)
    assert distance_m(lat, lon, glat, glon) == pytest.approx(30, rel=0.01)
    assert glon > lon and glat == pytest.approx(lat, abs=1e-6)
    assert ground_point(lat, lon, 30, -5, 90) == (lat, lon)  # 接近水平不投影
    assert ground_point(None, None, 30, -45, 0) is None


def test_offset_and_nearest():
    lat, lon = 34.3, 108.08
    north = offset(lat, lon, east_m=0, north_m=10)
    assert distance_m(lat, lon, *north) == pytest.approx(10, rel=1e-3)
    trees = [(1, *offset(lat, lon, 0, 0)), (2, *offset(lat, lon, 4, 0)), (3, *offset(lat, lon, 8, 0))]
    probe = offset(lat, lon, 3.2, 0.5)
    tree_id, dist = nearest(probe[0], probe[1], trees, max_distance_m=3)
    assert tree_id == 2 and dist < 1.5
    far = offset(lat, lon, 30, 30)
    assert nearest(far[0], far[1], trees, max_distance_m=3) is None
    assert nearest(lat, lon, [], 3) is None
