"""果树档案：照片与果树的自动关联、最新状态查询、CSV 批量导入。"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.drone.geo import nearest
from app.models import FruitTree, ImageStatus, InspectionImage

Candidate = tuple[int, float, float]

# CSV 表头别名（支持中英文）
CSV_COLUMNS: dict[str, tuple[str, ...]] = {
    "code": ("code", "编号", "果树编号", "树号", "id"),
    "latitude": ("latitude", "lat", "纬度"),
    "longitude": ("longitude", "lon", "lng", "经度"),
    "variety": ("variety", "品种"),
    "orchard": ("orchard", "果园"),
    "plot": ("plot", "地块", "片区"),
    "planted_year": ("planted_year", "定植年份", "种植年份"),
    "notes": ("notes", "备注"),
}


def tree_candidates(session: Session) -> list[Candidate]:
    rows = session.execute(
        select(FruitTree.id, FruitTree.latitude, FruitTree.longitude).where(
            FruitTree.latitude.is_not(None), FruitTree.longitude.is_not(None)
        )
    ).all()
    return [(r[0], r[1], r[2]) for r in rows]


def match_image(image: InspectionImage, candidates: list[Candidate], radius_m: float) -> bool:
    """按画面中心地面坐标（无则用飞机坐标）匹配最近的果树。"""
    lat = image.ground_latitude if image.ground_latitude is not None else image.latitude
    lon = image.ground_longitude if image.ground_longitude is not None else image.longitude
    found = nearest(lat, lon, candidates, radius_m) if lat is not None and lon is not None else None
    if found:
        image.tree_id, image.tree_distance_m = found[0], round(found[1], 2)
        return True
    image.tree_id, image.tree_distance_m = None, None
    return False


def rematch_all(session: Session, radius_m: float) -> int:
    """重新为所有带坐标的照片匹配果树，返回匹配成功的数量。"""
    candidates = tree_candidates(session)
    matched = 0
    images = session.scalars(select(InspectionImage).where(InspectionImage.latitude.is_not(None)))
    for image in images:
        matched += match_image(image, candidates, radius_m)
    session.commit()
    return matched


@dataclass
class TreeStatus:
    image_count: int = 0
    last_image: InspectionImage | None = None


def tree_status(session: Session, tree_ids: list[int]) -> dict[int, TreeStatus]:
    """查询每棵树的照片数量和最近一次已完成分析的照片。"""
    result = {tid: TreeStatus() for tid in tree_ids}
    if not tree_ids:
        return result
    counts = session.execute(
        select(InspectionImage.tree_id, func.count())
        .where(InspectionImage.tree_id.in_(tree_ids))
        .group_by(InspectionImage.tree_id)
    ).all()
    for tid, count in counts:
        result[tid].image_count = count
    observed = func.coalesce(InspectionImage.captured_at, InspectionImage.uploaded_at)
    latest = (
        select(InspectionImage.tree_id, func.max(observed).label("ts"))
        .where(InspectionImage.tree_id.in_(tree_ids), InspectionImage.status == ImageStatus.DONE)
        .group_by(InspectionImage.tree_id)
        .subquery()
    )
    rows = session.scalars(
        select(InspectionImage)
        .join(latest, (InspectionImage.tree_id == latest.c.tree_id) & (observed == latest.c.ts))
        .where(InspectionImage.status == ImageStatus.DONE)
        .order_by(InspectionImage.id)
    ).all()
    for image in rows:
        result[image.tree_id].last_image = image
    return result


def _decode_csv(content: bytes) -> str:
    # Excel 在中文 Windows 下默认把 CSV 保存为 GBK
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别 CSV 文件编码，请保存为 UTF-8 格式")


def parse_tree_csv(content: bytes) -> tuple[list[dict], list[str]]:
    """解析果树 CSV，返回 (记录列表, 错误信息列表)。"""
    reader = csv.DictReader(io.StringIO(_decode_csv(content)))
    if not reader.fieldnames:
        return [], ["CSV 文件为空"]
    header_map: dict[str, str] = {}
    for raw in reader.fieldnames:
        key = (raw or "").strip().lower()
        for field, aliases in CSV_COLUMNS.items():
            if key in aliases:
                header_map[raw] = field
    if "code" not in header_map.values():
        return [], ["CSV 缺少“编号/code”列"]

    records, errors = [], []
    for line_no, row in enumerate(reader, start=2):
        data = {field: (row.get(raw) or "").strip() for raw, field in header_map.items()}
        if not data.get("code"):
            if any(data.values()):
                errors.append(f"第 {line_no} 行：缺少编号")
            continue
        try:
            record: dict = {"code": data["code"][:60]}
            for field in ("latitude", "longitude"):
                if data.get(field):
                    record[field] = float(data[field])
            if data.get("planted_year"):
                record["planted_year"] = int(float(data["planted_year"]))
            for field in ("variety", "orchard", "plot", "notes"):
                if data.get(field):
                    record[field] = data[field]
        except ValueError:
            errors.append(f"第 {line_no} 行：数值格式错误")
            continue
        lat, lon = record.get("latitude"), record.get("longitude")
        if (lat is not None and not -90 <= lat <= 90) or (lon is not None and not -180 <= lon <= 180):
            errors.append(f"第 {line_no} 行：经纬度超出范围")
            continue
        records.append(record)
    return records, errors


def upsert_trees(session: Session, records: list[dict]) -> tuple[int, int]:
    created = updated = 0
    existing = {t.code: t for t in session.scalars(select(FruitTree).where(FruitTree.code.in_([r["code"] for r in records])))}
    for record in records:
        tree = existing.get(record["code"])
        if tree is None:
            tree = FruitTree(**record)
            session.add(tree)
            existing[tree.code] = tree
            created += 1
        else:
            for key, value in record.items():
                setattr(tree, key, value)
            updated += 1
    session.commit()
    return created, updated
