"""数据库对象 -> 接口返回结构 的转换。"""

from __future__ import annotations

import json

from app.analysis import knowledge as kb
from app.analysis.result import AnalysisResult
from app.models import FruitTree, InspectionImage, InspectionTask
from app.schemas import ImageDetail, ImageSummary, TaskOut, TaskStats, TreeHistoryPoint, TreeOut
from app.services.trees import TreeStatus

API = "/api/v1"


def image_urls(image: InspectionImage) -> dict[str, str | None]:
    base = f"{API}/images/{image.id}"
    return {
        "image_url": f"{base}/file",
        "thumbnail_url": f"{base}/thumbnail",
        "overlay_url": f"{base}/overlay" if image.overlay_path else None,
    }


def image_summary(image: InspectionImage) -> ImageSummary:
    return ImageSummary(
        id=image.id,
        task_id=image.task_id,
        task_name=image.task.name if image.task else None,
        tree_id=image.tree_id,
        tree_code=image.tree.code if image.tree else None,
        original_filename=image.original_filename,
        status=image.status,
        error=image.error,
        uploaded_at=image.uploaded_at,
        captured_at=image.captured_at,
        analyzed_at=image.analyzed_at,
        latitude=image.latitude,
        longitude=image.longitude,
        altitude_rel=image.altitude_rel,
        health_score=image.health_score,
        health_level=image.health_level,
        health_level_label=kb.HEALTH_LEVELS.get(image.health_level) if image.health_level else None,
        growth_stage=image.growth_stage,
        growth_stage_label=kb.GROWTH_STAGES.get(image.growth_stage) if image.growth_stage else None,
        vigor_level=image.vigor_level,
        vigor_label=kb.VIGOR_LEVELS.get(image.vigor_level) if image.vigor_level else None,
        primary_issue=image.primary_issue,
        **image_urls(image),
    )


def image_detail(image: InspectionImage) -> ImageDetail:
    result = AnalysisResult.model_validate_json(image.result_json) if image.result_json else None
    metadata = json.loads(image.metadata_json) if image.metadata_json else {}
    return ImageDetail(
        **image_summary(image).model_dump(),
        width=image.width,
        height=image.height,
        file_size=image.file_size,
        image_format=image.image_format,
        camera_make=image.camera_make,
        camera_model=image.camera_model,
        focal_length_mm=image.focal_length_mm,
        focal_length_35mm=image.focal_length_35mm,
        gimbal_pitch=image.gimbal_pitch,
        gimbal_yaw=image.gimbal_yaw,
        flight_yaw=image.flight_yaw,
        altitude_abs=image.altitude_abs,
        ground_latitude=image.ground_latitude,
        ground_longitude=image.ground_longitude,
        gsd_cm=image.gsd_cm,
        tree_distance_m=image.tree_distance_m,
        metadata=metadata,
        result=result,
    )


def task_out(task: InspectionTask, stats: TaskStats | None = None) -> TaskOut:
    out = TaskOut.model_validate(task)
    if stats is not None:
        out.stats = stats
    return out


def tree_out(tree: FruitTree, status: TreeStatus | None = None) -> TreeOut:
    out = TreeOut.model_validate(tree)
    if status is not None:
        out.image_count = status.image_count
        last = status.last_image
        if last is not None:
            out.last_image_id = last.id
            out.last_inspected_at = last.observed_at
            out.last_health_score = last.health_score
            out.last_health_level = last.health_level
            out.last_health_level_label = kb.HEALTH_LEVELS.get(last.health_level or "")
            out.last_growth_stage_label = kb.GROWTH_STAGES.get(last.growth_stage or "")
    return out


def tree_history_point(image: InspectionImage) -> TreeHistoryPoint:
    return TreeHistoryPoint(
        image_id=image.id,
        task_id=image.task_id,
        observed_at=image.observed_at,
        health_score=image.health_score,
        health_level=image.health_level,
        health_level_label=kb.HEALTH_LEVELS.get(image.health_level or ""),
        growth_stage_label=kb.GROWTH_STAGES.get(image.growth_stage or ""),
        vigor_label=kb.VIGOR_LEVELS.get(image.vigor_level or ""),
        primary_issue=image.primary_issue,
        thumbnail_url=f"{API}/images/{image.id}/thumbnail",
    )
