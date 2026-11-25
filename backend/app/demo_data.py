"""演示/测试用的“模拟无人机照片”生成器。

生成俯拍视角的果园场景（土壤背景 + 若干树冠），并写入与大疆无人机一致的
EXIF GPS 信息和 drone-dji XMP 元数据，可用于体验系统流程和自动化测试。
注意：这些是程序合成的图像，仅用于演示，不代表真实果园数据。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SCENES = (
    "healthy",  # 健康
    "chlorosis",  # 黄化
    "necrosis",  # 褐变坏死
    "leaf_spots",  # 病斑
    "sparse",  # 冠层稀疏
    "flowering",  # 开花
    "fruiting",  # 挂果
    "dormant",  # 休眠（无叶）
)

SOIL = np.array([148, 116, 84], dtype=np.float32)


def _soil(rng: np.random.Generator, w: int, h: int) -> np.ndarray:
    noise = rng.normal(0, 9, size=(h // 8 + 1, w // 8 + 1, 1)).astype(np.float32)
    coarse = np.asarray(
        Image.fromarray(np.clip(noise[..., 0] + 128, 0, 255).astype(np.uint8)).resize((w, h), Image.BILINEAR),
        dtype=np.float32,
    )[..., None] - 128
    fine = rng.normal(0, 6, size=(h, w, 1)).astype(np.float32)
    return np.clip(SOIL + coarse + fine, 0, 255)


def _jitter(rng: np.random.Generator, color: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    return tuple(int(np.clip(c * (1 + rng.uniform(-amount, amount)), 0, 255)) for c in color)  # type: ignore[return-value]


def make_scene(kind: str = "healthy", size: tuple[int, int] = (1600, 1200), seed: int = 0) -> Image.Image:
    """生成一张模拟的正射果园照片。"""
    if kind not in SCENES:
        raise ValueError(f"未知场景 {kind}，可选：{', '.join(SCENES)}")
    rng = np.random.default_rng(seed)
    w, h = size
    img = Image.fromarray(_soil(rng, w, h).astype(np.uint8))
    draw = ImageDraw.Draw(img)

    cols, rows = 3, 2
    radius = min(w / cols, h / rows) * 0.36
    centers = [
        ((c + 0.5) * w / cols + rng.uniform(-0.05, 0.05) * w / cols,
         (r + 0.5) * h / rows + rng.uniform(-0.05, 0.05) * h / rows)
        for r in range(rows)
        for c in range(cols)
    ]

    for cx, cy in centers:
        # 树影
        draw.ellipse([cx - radius + radius * 0.25, cy - radius + radius * 0.3,
                      cx + radius + radius * 0.25, cy + radius + radius * 0.3], fill=(92, 74, 56))
        if kind == "dormant":
            for _ in range(14):
                angle = rng.uniform(0, 2 * math.pi)
                length = radius * rng.uniform(0.5, 1.0)
                draw.line([cx, cy, cx + math.cos(angle) * length, cy + math.sin(angle) * length],
                          fill=_jitter(rng, (96, 82, 70), 0.1), width=max(2, int(radius * 0.04)))
            continue

        sparse = kind == "sparse"
        for _ in range(30 if sparse else 170):
            dist = radius * math.sqrt(rng.uniform(0, 1)) * 0.92
            angle = rng.uniform(0, 2 * math.pi)
            x, y = cx + math.cos(angle) * dist, cy + math.sin(angle) * dist
            rr = radius * (rng.uniform(0.08, 0.15) if sparse else rng.uniform(0.1, 0.2))
            light = 1.0 + 0.25 * (-(x - cx) - (y - cy)) / (radius * 1.4)  # 左上方受光
            base = (56 * light, 116 * light, 40 * light)
            draw.ellipse([x - rr, y - rr, x + rr, y + rr], fill=_jitter(rng, base, 0.12))

        def scatter(count: int, color: tuple[int, int, int], r_min: float, r_max: float, spread: float = 0.85,
                    amount: float = 0.08, cx: float = cx, cy: float = cy) -> None:
            for _ in range(count):
                dist = radius * math.sqrt(rng.uniform(0, 1)) * spread
                angle = rng.uniform(0, 2 * math.pi)
                x, y = cx + math.cos(angle) * dist, cy + math.sin(angle) * dist
                rr = rng.uniform(r_min, r_max)
                draw.ellipse([x - rr, y - rr, x + rr, y + rr], fill=_jitter(rng, color, amount))

        if kind == "chlorosis":
            scatter(45, (214, 192, 48), radius * 0.08, radius * 0.17)
        elif kind == "necrosis":
            scatter(24, (128, 72, 34), radius * 0.07, radius * 0.14, spread=0.7)
        elif kind == "leaf_spots":
            scatter(70, (205, 180, 40), 2, 4, amount=0.05)
            scatter(50, (125, 70, 32), 2, 4, amount=0.05)
        elif kind == "flowering":
            scatter(260, (246, 244, 240), radius * 0.025, radius * 0.05, amount=0.02)
            scatter(120, (240, 190, 205), radius * 0.025, radius * 0.045, amount=0.03)
        elif kind == "fruiting":
            scatter(70, (205, 32, 38), radius * 0.03, radius * 0.05, amount=0.06)

    img = img.filter(ImageFilter.GaussianBlur(0.6))
    arr = np.asarray(img, dtype=np.float32)
    arr += rng.normal(0, 4, size=arr.shape).astype(np.float32)  # 传感器噪声
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _dms(value: float) -> tuple[float, float, float]:
    value = abs(value)
    deg = int(value)
    minutes_full = (value - deg) * 60
    minutes = int(minutes_full)
    return float(deg), float(minutes), round((minutes_full - minutes) * 60, 4)


@dataclass
class DroneShot:
    latitude: float
    longitude: float
    captured_at: datetime
    relative_altitude: float = 30.0
    absolute_altitude: float = 450.0
    gimbal_pitch: float = -90.0
    gimbal_yaw: float = 0.0
    flight_yaw: float = 0.0
    make: str = "DJI"
    model: str = "FC6310"
    focal_length: float = 8.8
    focal_length_35mm: int = 24


def drone_xmp(shot: DroneShot) -> bytes:
    xmp = f"""<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="DJI Meta Data"
    xmlns:tiff="http://ns.adobe.com/tiff/1.0/"
    xmlns:drone-dji="http://www.dji.com/drone-dji/1.0/"
   tiff:Make="{shot.make}"
   tiff:Model="{shot.model}"
   drone-dji:AbsoluteAltitude="{shot.absolute_altitude:+.2f}"
   drone-dji:RelativeAltitude="{shot.relative_altitude:+.2f}"
   drone-dji:GpsLatitude="{shot.latitude:.8f}"
   drone-dji:GpsLongitude="{shot.longitude:.8f}"
   drone-dji:GimbalRollDegree="+0.00"
   drone-dji:GimbalYawDegree="{shot.gimbal_yaw:+.2f}"
   drone-dji:GimbalPitchDegree="{shot.gimbal_pitch:+.2f}"
   drone-dji:FlightRollDegree="+0.00"
   drone-dji:FlightYawDegree="{shot.flight_yaw:+.2f}"
   drone-dji:FlightPitchDegree="+0.00"
   drone-dji:RtkFlag="50"/>
 </rdf:RDF>
</x:xmpmeta>"""
    return xmp.encode("utf-8")


def save_drone_photo(img: Image.Image, path: str | Path, shot: DroneShot, quality: int = 92) -> Path:
    """把图像保存为带无人机 EXIF/XMP 元数据的 JPEG。"""
    path = Path(path)
    stamp = shot.captured_at.strftime("%Y:%m:%d %H:%M:%S")
    exif = Image.Exif()
    exif[0x010F] = shot.make
    exif[0x0110] = shot.model
    exif[0x0132] = stamp
    exif[0x8769] = {0x9003: stamp, 0x920A: shot.focal_length, 0xA405: shot.focal_length_35mm}
    exif[0x8825] = {
        0: b"\x02\x03\x00\x00",
        1: "N" if shot.latitude >= 0 else "S",
        2: _dms(shot.latitude),
        3: "E" if shot.longitude >= 0 else "W",
        4: _dms(shot.longitude),
        5: b"\x00",
        6: round(shot.absolute_altitude, 2),
    }
    img.save(path, "JPEG", quality=quality, exif=exif, xmp=drone_xmp(shot))
    return path


def offset_position(lat: float, lon: float, east_m: float, north_m: float) -> tuple[float, float]:
    """在 (lat, lon) 基础上向东/北偏移若干米。"""
    dlat = north_m / 111_320.0
    dlon = east_m / (111_320.0 * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


# 演示果园：8 棵树、4 次巡检（4 月花期 → 8 月挂果），部分树出现病害后恢复或加重
DEMO_FLIGHTS: tuple[datetime, ...] = (
    datetime(2026, 4, 12, 9, 30),
    datetime(2026, 6, 10, 9, 20),
    datetime(2026, 7, 15, 9, 40),
    datetime(2026, 8, 28, 9, 10),
)
DEMO_SCHEDULE: tuple[tuple[str, ...], ...] = (
    ("flowering", "healthy", "healthy", "fruiting"),
    ("flowering", "healthy", "chlorosis", "chlorosis"),
    ("flowering", "leaf_spots", "necrosis", "necrosis"),
    ("flowering", "healthy", "sparse", "sparse"),
    ("flowering", "healthy", "healthy", "fruiting"),
    ("flowering", "leaf_spots", "healthy", "healthy"),
    ("flowering", "chlorosis", "healthy", "fruiting"),
    ("flowering", "healthy", "healthy", "healthy"),
)


def demo_tree_positions(
    origin: tuple[float, float] = (34.30215, 108.08571), spacing_m: float = 6.0
) -> list[tuple[str, float, float]]:
    """演示果树的编号与坐标（2 行 × 4 株）。"""
    trees = []
    for i in range(len(DEMO_SCHEDULE)):
        lat, lon = offset_position(origin[0], origin[1], east_m=(i % 4) * spacing_m, north_m=(i // 4) * spacing_m)
        trees.append((f"A1-{i // 4 + 1:02d}-{i % 4 + 1:02d}", lat, lon))
    return trees


def generate_demo_set(
    out_dir: str | Path,
    *,
    origin: tuple[float, float] = (34.30215, 108.08571),
    size: tuple[int, int] = (1600, 1200),
    spacing_m: float = 6.0,
    flights: int = len(DEMO_FLIGHTS),
) -> list[Path]:
    """生成演示数据：每次巡检一个文件夹（文件夹名即任务编号），并附带果树档案 trees.csv。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    trees = demo_tree_positions(origin, spacing_m)
    lines = ["编号,纬度,经度,品种,果园,地块,定植年份"]
    lines += [f"{code},{lat:.8f},{lon:.8f},红富士,示范果园,A1区,2016" for code, lat, lon in trees]
    (out / "trees.csv").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")

    paths = []
    for f, start in enumerate(DEMO_FLIGHTS[:flights]):
        folder = out / f"DJI_{start:%Y%m%d%H%M}_A1区巡检"
        folder.mkdir(exist_ok=True)
        for i, (_code, lat, lon) in enumerate(trees):
            kind = DEMO_SCHEDULE[i][f]
            captured = start + timedelta(seconds=9 * i)
            shot = DroneShot(latitude=lat, longitude=lon, captured_at=captured, relative_altitude=25.0,
                             gimbal_yaw=90.0, flight_yaw=90.0)
            img = make_scene(kind, size=size, seed=1000 * f + i)
            paths.append(save_drone_photo(img, folder / f"DJI_{captured:%Y%m%d%H%M%S}_{i + 1:04d}.JPG", shot))
    return paths
