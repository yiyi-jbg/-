"""地理坐标计算：距离、坐标偏移、倾斜拍摄时的画面中心地面点估算、最近果树匹配。"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

EARTH_RADIUS_M = 6_371_008.8


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点间球面距离（米，haversine 公式）。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def offset(lat: float, lon: float, east_m: float, north_m: float) -> tuple[float, float]:
    """从 (lat, lon) 向东、向北各移动若干米后的坐标（小范围近似）。"""
    dlat = math.degrees(north_m / EARTH_RADIUS_M)
    dlon = math.degrees(east_m / (EARTH_RADIUS_M * math.cos(math.radians(lat))))
    return lat + dlat, lon + dlon


def ground_point(
    lat: float | None,
    lon: float | None,
    relative_altitude: float | None,
    gimbal_pitch: float | None,
    gimbal_yaw: float | None,
) -> tuple[float, float] | None:
    """估算画面中心对应的地面坐标。

    正射（云台 -90°）时即为飞机正下方；倾斜拍摄时，沿云台朝向偏移
    距离 = 相对高度 / tan(|俯仰角|)。俯仰角接近水平（> -20°）时无法可靠投影，返回飞机位置。
    """
    if lat is None or lon is None:
        return None
    if relative_altitude is None or gimbal_pitch is None or gimbal_yaw is None:
        return lat, lon
    if gimbal_pitch <= -85 or gimbal_pitch > -20 or relative_altitude <= 0:
        return lat, lon
    dist = relative_altitude / math.tan(math.radians(-gimbal_pitch))
    yaw = math.radians(gimbal_yaw)
    return offset(lat, lon, east_m=dist * math.sin(yaw), north_m=dist * math.cos(yaw))


def nearest(
    lat: float,
    lon: float,
    candidates: Sequence[tuple[int, float, float]],
    max_distance_m: float,
) -> tuple[int, float] | None:
    """在候选点 (id, lat, lon) 中查找距离最近且不超过阈值的点，返回 (id, 距离)。"""
    if not candidates:
        return None
    arr = np.asarray([(c[1], c[2]) for c in candidates], dtype=np.float64)
    lat_r = np.radians(arr[:, 0])
    dlat = lat_r - math.radians(lat)
    dlon = np.radians(arr[:, 1] - lon)
    a = np.sin(dlat / 2) ** 2 + math.cos(math.radians(lat)) * np.cos(lat_r) * np.sin(dlon / 2) ** 2
    dist = 2 * EARTH_RADIUS_M * np.arcsin(np.minimum(1.0, np.sqrt(a)))
    idx = int(np.argmin(dist))
    if dist[idx] > max_distance_m:
        return None
    return candidates[idx][0], float(dist[idx])
