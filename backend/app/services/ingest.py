"""照片入库：保存文件、去重、解析无人机元数据、生成缩略图、关联果树。

网页上传和无人机自动推送共用这一流程。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import BinaryIO

from PIL import UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.drone.geo import ground_point
from app.drone.metadata import compute_gsd_cm, read_metadata
from app.models import ImageStatus, InspectionImage, InspectionTask
from app.schemas import UploadItem
from app.services.storage import Storage, UploadTooLargeError
from app.services.trees import match_image, tree_candidates

logger = logging.getLogger(__name__)

UNSUPPORTED_MODES = ("1", "L", "LA", "I", "F")


@dataclass
class IncomingFile:
    filename: str
    stream: BinaryIO


def ingest_files(
    session: Session,
    storage: Storage,
    settings: Settings,
    task: InspectionTask,
    files: list[IncomingFile],
) -> tuple[list[UploadItem], list[int]]:
    """逐个处理上传文件，返回 (每个文件的处理结果, 新入库的照片 ID)。"""
    items: list[UploadItem] = []
    accepted: list[int] = []
    candidates = tree_candidates(session)

    for incoming in files:
        name = (incoming.filename or "image.jpg").strip()[:255]
        try:
            saved = storage.save_stream(task.id, incoming.stream, name)
        except UploadTooLargeError:
            items.append(UploadItem(filename=name, status="rejected", error=f"文件超过 {settings.max_upload_mb}MB 上限"))
            continue

        existing_id = session.scalar(
            select(InspectionImage.id).where(InspectionImage.task_id == task.id, InspectionImage.sha256 == saved.sha256)
        )
        if existing_id:
            storage.delete(saved.rel_path)
            items.append(UploadItem(filename=name, status="duplicate", image_id=existing_id, error="该照片已上传过"))
            continue

        try:
            meta = read_metadata(storage.path(saved.rel_path))
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
            storage.delete(saved.rel_path)
            items.append(UploadItem(filename=name, status="rejected", error="不是有效的图片文件"))
            continue
        if meta.mode in UNSUPPORTED_MODES or (meta.mode or "").startswith("I;"):
            storage.delete(saved.rel_path)
            items.append(
                UploadItem(filename=name, status="rejected", error="暂不支持单波段（灰度/多光谱）影像，请上传可见光 RGB 照片")
            )
            continue

        image = InspectionImage(
            task_id=task.id,
            original_filename=name,
            stored_path=saved.rel_path,
            file_size=saved.size,
            sha256=saved.sha256,
            image_format=meta.format,
            width=meta.width,
            height=meta.height,
            captured_at=meta.captured_at,
            latitude=meta.latitude,
            longitude=meta.longitude,
            altitude_abs=meta.altitude_abs,
            altitude_rel=meta.altitude_rel,
            camera_make=meta.camera_make,
            camera_model=meta.camera_model,
            focal_length_mm=meta.focal_length_mm,
            focal_length_35mm=meta.focal_length_35mm,
            gimbal_pitch=meta.gimbal_pitch,
            gimbal_yaw=meta.gimbal_yaw,
            flight_yaw=meta.flight_yaw,
            metadata_json=json.dumps(meta.to_dict(), ensure_ascii=False),
            status=ImageStatus.PENDING,
        )
        height_m = meta.altitude_rel if meta.altitude_rel and meta.altitude_rel > 0 else task.flight_height_m
        gsd = compute_gsd_cm(height_m, meta.focal_length_35mm, meta.width, meta.height, meta.gimbal_pitch)
        image.gsd_cm = round(gsd, 4) if gsd else None
        point = ground_point(meta.latitude, meta.longitude, meta.altitude_rel, meta.gimbal_pitch, meta.gimbal_yaw)
        if point:
            image.ground_latitude, image.ground_longitude = point
        match_image(image, candidates, settings.tree_match_radius_m)

        session.add(image)
        try:
            session.flush()
        except IntegrityError:  # 并发上传同一张照片
            session.rollback()
            storage.delete(saved.rel_path)
            existing_id = session.scalar(
                select(InspectionImage.id).where(
                    InspectionImage.task_id == task.id, InspectionImage.sha256 == saved.sha256
                )
            )
            items.append(UploadItem(filename=name, status="duplicate", image_id=existing_id, error="该照片已上传过"))
            continue

        try:
            image.thumbnail_path = storage.make_thumbnail(saved.rel_path, task.id, image.id)
        except Exception:  # 缩略图失败不影响入库
            logger.warning("生成缩略图失败：%s", name, exc_info=True)
        session.commit()
        accepted.append(image.id)
        items.append(UploadItem(filename=name, status="accepted", image_id=image.id))

    return items, accepted
