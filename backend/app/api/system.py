"""系统信息、统计看板与即时分析接口。"""

from __future__ import annotations

import base64
import io

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app import __version__
from app.analysis import AnalysisContext, UnsupportedImageError
from app.analysis import knowledge as kb
from app.api.deps import get_session, get_settings, require_api_key
from app.config import Settings
from app.drone.metadata import compute_gsd_cm, read_metadata
from app.schemas import AnalyzeResponse, MapData, OverviewStats, SystemInfo
from app.services import stats as stats_service

router = APIRouter(tags=["系统与统计"])


@router.get("/system/info", response_model=SystemInfo, summary="系统信息与枚举值中文名称")
def system_info(request: Request, settings: Settings = Depends(get_settings)) -> SystemInfo:
    analyzer = request.app.state.analyzer
    return SystemInfo(
        app_name=settings.app_name,
        version=__version__,
        analyzer=analyzer.description,
        model_loaded=analyzer.model is not None,
        api_key_required=bool(settings.api_key),
        max_upload_mb=settings.max_upload_mb,
        tree_match_radius_m=settings.tree_match_radius_m,
        workers=settings.workers,
        enums=kb.enum_labels(),
    )


@router.get("/stats/overview", response_model=OverviewStats, summary="总览统计")
def overview(
    task_id: int | None = None,
    days: int = Query(30, ge=1, le=366, description="健康趋势统计最近多少个巡检日"),
    session: Session = Depends(get_session),
) -> OverviewStats:
    return stats_service.overview(session, task_id=task_id, days=days)


@router.get("/stats/map", response_model=MapData, summary="照片与果树的位置分布")
def map_points(task_id: int | None = None, session: Session = Depends(get_session)) -> MapData:
    return stats_service.map_data(session, task_id=task_id)


@router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    summary="即时分析单张照片（不保存）",
    description="上传一张照片，立即返回元数据与分析结果，适合机载边缘设备或第三方系统直接调用。",
    dependencies=[Depends(require_api_key)],
)
def analyze_once(
    request: Request,
    file: UploadFile = File(...),
    include_overlay: bool = Query(False, description="是否返回 Base64 编码的标注图"),
    flight_height_m: float | None = Query(None, gt=0, le=1000, description="照片缺少相对高度时使用的飞行高度"),
    settings: Settings = Depends(get_settings),
) -> AnalyzeResponse:
    data = file.file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"文件超过 {settings.max_upload_mb}MB 上限")
    try:
        with Image.open(io.BytesIO(data)) as img:
            meta = read_metadata(img)
        height = meta.altitude_rel if meta.altitude_rel and meta.altitude_rel > 0 else flight_height_m
        gsd = compute_gsd_cm(height, meta.focal_length_35mm, meta.width, meta.height, meta.gimbal_pitch)
        output = request.app.state.analyzer.analyze(
            data, AnalysisContext(captured_at=meta.captured_at, latitude=meta.latitude, gsd_cm=gsd)
        )
    except UnsupportedImageError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="不是有效的图片文件") from None

    overlay_b64 = None
    if include_overlay:
        buf = io.BytesIO()
        output.overlay.save(buf, "JPEG", quality=85)
        overlay_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return AnalyzeResponse(metadata=meta.to_dict(), result=output.result, overlay_base64=overlay_b64)
