from __future__ import annotations

import io
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.demo_data import DroneShot, make_scene, save_drone_photo
from app.main import create_app

ORIGIN = (34.30215, 108.08571)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", process_inline=True, workers=1)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


PhotoFactory = Callable[..., bytes]


@pytest.fixture
def photo(tmp_path: Path) -> PhotoFactory:
    """生成一张带无人机元数据的模拟照片，返回 JPEG 字节。"""
    counter = {"n": 0}

    def factory(
        kind: str = "healthy",
        *,
        captured_at: datetime = datetime(2026, 7, 15, 9, 30),
        latitude: float = ORIGIN[0],
        longitude: float = ORIGIN[1],
        seed: int = 1,
        size: tuple[int, int] = (800, 600),
        **shot_kwargs,
    ) -> bytes:
        counter["n"] += 1
        path = tmp_path / f"photo_{counter['n']}.jpg"
        shot = DroneShot(latitude=latitude, longitude=longitude, captured_at=captured_at, **shot_kwargs)
        save_drone_photo(make_scene(kind, size=size, seed=seed), path, shot)
        return path.read_bytes()

    return factory


def jpeg_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()
