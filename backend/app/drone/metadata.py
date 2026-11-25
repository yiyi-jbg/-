"""无人机照片元数据解析。

- EXIF：拍摄时间、GPS 经纬度/海拔、相机型号、焦距（含 35mm 等效焦距）；
- 大疆 XMP（drone-dji 命名空间）：相对起飞点高度、云台俯仰/偏航角、飞行器姿态、RTK 状态等。

其他品牌无人机只要写入标准 EXIF GPS 信息即可使用；缺少相对高度时，
可在巡检任务中填写“飞行高度”用于估算地面分辨率。
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

# 全画幅（36×24mm）对角线长度，用于 35mm 等效焦距换算
FULL_FRAME_DIAGONAL_MM = math.hypot(36.0, 24.0)

_XMP_ATTR = re.compile(r'([A-Za-z][\w-]*):([A-Za-z]\w*)\s*=\s*"([^"]*)"')
_XMP_ELEM = re.compile(r"<([A-Za-z][\w-]*):([A-Za-z]\w*)>([^<]*)</\1:\2>")
_XMP_KEYS = {
    "AbsoluteAltitude",
    "RelativeAltitude",
    "GpsLatitude",
    "GpsLongitude",
    "GpsLongtitude",  # 部分大疆机型的拼写
    "GimbalRollDegree",
    "GimbalYawDegree",
    "GimbalPitchDegree",
    "FlightRollDegree",
    "FlightYawDegree",
    "FlightPitchDegree",
    "RtkFlag",
    "RtkStdLon",
    "RtkStdLat",
    "RtkStdHgt",
    "CalibratedFocalLength",
    "DroneModel",
    "DroneSerialNumber",
    "CameraSerialNumber",
    "Make",
    "Model",
}


@dataclass
class PhotoMetadata:
    width: int
    height: int
    format: str | None = None
    mode: str | None = None
    captured_at: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    altitude_abs: float | None = None
    altitude_rel: float | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    focal_length_mm: float | None = None
    focal_length_35mm: float | None = None
    gimbal_pitch: float | None = None
    gimbal_yaw: float | None = None
    gimbal_roll: float | None = None
    flight_yaw: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_gps(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.captured_at:
            data["captured_at"] = self.captured_at.isoformat()
        return data


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("ascii", errors="ignore")
        if isinstance(value, str):
            value = value.strip().lstrip("+")
        result = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return result if math.isfinite(result) else None


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    text = str(value).replace("\x00", "").strip()
    return text or None


def _parse_datetime(value: Any) -> datetime | None:
    text = _clean_str(value)
    if not text or text.startswith("0000"):
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def _dms_to_degrees(value: Any, ref: Any) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, (tuple, list)):
            parts = [float(v) for v in value]
            while len(parts) < 3:
                parts.append(0.0)
            degrees = parts[0] + parts[1] / 60.0 + parts[2] / 3600.0
        else:
            degrees = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    ref_text = _clean_str(ref) or ""
    if ref_text.upper() in ("S", "W"):
        degrees = -degrees
    return degrees if math.isfinite(degrees) else None


def _valid_coordinate(lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return False
    return not (abs(lat) < 1e-9 and abs(lon) < 1e-9)  # 0,0 通常表示未定位


def parse_xmp(xmp: str) -> dict[str, str]:
    """提取 XMP 中我们关心的字段（属性形式和元素形式都支持）。"""
    fields: dict[str, str] = {}
    for _ns, key, value in [*_XMP_ATTR.findall(xmp), *_XMP_ELEM.findall(xmp)]:
        if key in _XMP_KEYS and key not in fields:
            fields[key] = value.strip()
    return fields


def _read_xmp(img: Image.Image, path: Path | None) -> str | None:
    xmp = img.info.get("xmp") or img.info.get("XML:com.adobe.xmp")
    if isinstance(xmp, (bytes, bytearray)):
        return xmp.decode("utf-8", errors="ignore")
    if isinstance(xmp, str):
        return xmp
    if path is None:
        return None
    # 兜底：直接在文件头部查找 XMP 包
    with open(path, "rb") as fh:
        head = fh.read(1024 * 1024)
    start = head.find(b"<x:xmpmeta")
    end = head.find(b"</x:xmpmeta>", start)
    if start < 0 or end < 0:
        return None
    return head[start : end + len(b"</x:xmpmeta>")].decode("utf-8", errors="ignore")


def read_metadata(source: str | Path | Image.Image) -> PhotoMetadata:
    """读取照片元数据（只解析文件头，不解码像素，速度很快）。"""
    if isinstance(source, Image.Image):
        return _extract(source, None)
    path = Path(source)
    with Image.open(path) as img:
        return _extract(img, path)


def _extract(img: Image.Image, path: Path | None) -> PhotoMetadata:
    meta = PhotoMetadata(width=img.width, height=img.height, format=img.format, mode=img.mode)
    exif = img.getexif()
    if exif:
        meta.camera_make = _clean_str(exif.get(0x010F))
        meta.camera_model = _clean_str(exif.get(0x0110))
        sub = exif.get_ifd(0x8769)
        meta.captured_at = _parse_datetime(sub.get(0x9003)) or _parse_datetime(exif.get(0x0132))
        meta.focal_length_mm = _to_float(sub.get(0x920A))
        meta.focal_length_35mm = _to_float(sub.get(0xA405)) or None
        orientation = exif.get(0x0112)
        if orientation in (5, 6, 7, 8):  # 旋转 90° 的照片，宽高互换
            meta.width, meta.height = meta.height, meta.width

        gps = exif.get_ifd(0x8825)
        if gps:
            lat = _dms_to_degrees(gps.get(2), gps.get(1))
            lon = _dms_to_degrees(gps.get(4), gps.get(3))
            if _valid_coordinate(lat, lon):
                meta.latitude, meta.longitude = lat, lon
            alt = _to_float(gps.get(6))
            if alt is not None:
                ref = gps.get(5)
                below = ref in (1, b"\x01", "1")
                meta.altitude_abs = -alt if below else alt

    xmp_text = _read_xmp(img, path)
    if xmp_text:
        xmp = parse_xmp(xmp_text)
        if xmp:
            meta.raw["xmp"] = xmp
        lat = _to_float(xmp.get("GpsLatitude"))
        lon = _to_float(xmp.get("GpsLongitude") or xmp.get("GpsLongtitude"))
        if meta.latitude is None and _valid_coordinate(lat, lon):
            meta.latitude, meta.longitude = lat, lon
        meta.altitude_rel = _to_float(xmp.get("RelativeAltitude"))
        if meta.altitude_abs is None:
            meta.altitude_abs = _to_float(xmp.get("AbsoluteAltitude"))
        meta.gimbal_pitch = _to_float(xmp.get("GimbalPitchDegree"))
        meta.gimbal_yaw = _to_float(xmp.get("GimbalYawDegree"))
        meta.gimbal_roll = _to_float(xmp.get("GimbalRollDegree"))
        meta.flight_yaw = _to_float(xmp.get("FlightYawDegree"))
        meta.camera_make = meta.camera_make or _clean_str(xmp.get("Make"))
        meta.camera_model = meta.camera_model or _clean_str(xmp.get("Model"))
    return meta


def compute_gsd_cm(
    height_m: float | None,
    focal_length_35mm: float | None,
    width_px: int,
    height_px: int,
    gimbal_pitch: float | None = None,
) -> float | None:
    """估算正射照片的地面分辨率 GSD（厘米/像素）。

    GSD = 飞行高度 × 等效传感器宽度 / (35mm 等效焦距 × 图像宽度)。
    等效焦距按对角线换算，因此等效传感器宽度 = 43.27mm × 宽 / 对角线像素。
    云台未垂直向下（俯仰角大于 -70°）时画面内分辨率不均匀，不做估算。
    """
    if not height_m or height_m <= 0 or not focal_length_35mm or focal_length_35mm <= 0 or width_px <= 0:
        return None
    if gimbal_pitch is not None and gimbal_pitch > -70:
        return None
    sensor_width_mm = FULL_FRAME_DIAGONAL_MM * width_px / math.hypot(width_px, height_px)
    return height_m * sensor_width_mm / (focal_length_35mm * width_px) * 100.0
