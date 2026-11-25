"""巡检照片接口：查询、下载原图/缩略图/标注图、重新分析、删除。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_session, get_storage, get_worker, require_api_key
from app.models import ImageStatus, InspectionImage
from app.schemas import ImageDetail, ImageSummary, Page
from app.services.presenters import image_detail, image_summary
from app.services.processing import AnalysisWorker
from app.services.storage import Storage

router = APIRouter(prefix="/images", tags=["巡检照片"])

SORTS = {
    "newest": (InspectionImage.id.desc(),),
    "captured": (InspectionImage.captured_at.asc(), InspectionImage.id.asc()),
    "score_asc": (InspectionImage.health_score.is_(None), InspectionImage.health_score.asc(), InspectionImage.id.desc()),
    "score_desc": (InspectionImage.health_score.is_(None), InspectionImage.health_score.desc(), InspectionImage.id.desc()),
}


def get_image_or_404(session: Session, image_id: int) -> InspectionImage:
    image = session.get(
        InspectionImage, image_id, options=[joinedload(InspectionImage.task), joinedload(InspectionImage.tree)]
    )
    if image is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="照片不存在")
    return image


@router.get("", response_model=Page[ImageSummary], summary="照片列表（支持按任务、果树、健康等级筛选）")
def list_images(
    task_id: int | None = None,
    tree_id: int | None = None,
    health_level: str | None = Query(None, description="healthy/mild/moderate/severe/unknown，多个用逗号分隔"),
    growth_stage: str | None = None,
    status_: str | None = Query(None, alias="status", description="pending/processing/done/failed"),
    sort: str = Query("newest", pattern="^(newest|captured|score_asc|score_desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=500),
    session: Session = Depends(get_session),
) -> Page[ImageSummary]:
    stmt = select(InspectionImage)
    if task_id is not None:
        stmt = stmt.where(InspectionImage.task_id == task_id)
    if tree_id is not None:
        stmt = stmt.where(InspectionImage.tree_id == tree_id)
    if health_level:
        stmt = stmt.where(InspectionImage.health_level.in_([v for v in health_level.split(",") if v]))
    if growth_stage:
        stmt = stmt.where(InspectionImage.growth_stage == growth_stage)
    if status_:
        stmt = stmt.where(InspectionImage.status.in_([v for v in status_.split(",") if v]))
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    images = session.scalars(
        stmt.options(joinedload(InspectionImage.task), joinedload(InspectionImage.tree))
        .order_by(*SORTS[sort])
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return Page(items=[image_summary(i) for i in images], total=total, page=page, page_size=page_size)


@router.get("/{image_id}", response_model=ImageDetail, summary="照片详情（含完整分析结果）")
def get_image(image_id: int, session: Session = Depends(get_session)) -> ImageDetail:
    return image_detail(get_image_or_404(session, image_id))


def _file_response(storage: Storage, rel_path: str | None, filename: str | None = None) -> FileResponse:
    if not rel_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文件不存在")
    path = storage.path(rel_path)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="文件不存在")
    return FileResponse(path, filename=filename, content_disposition_type="inline",
                        headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{image_id}/file", summary="下载原图")
def get_image_file(image_id: int, session: Session = Depends(get_session),
                   storage: Storage = Depends(get_storage)) -> FileResponse:
    image = get_image_or_404(session, image_id)
    return _file_response(storage, image.stored_path, image.original_filename)


@router.get("/{image_id}/thumbnail", summary="缩略图")
def get_thumbnail(image_id: int, session: Session = Depends(get_session),
                  storage: Storage = Depends(get_storage)) -> FileResponse:
    image = get_image_or_404(session, image_id)
    return _file_response(storage, image.thumbnail_path or image.stored_path)


@router.get("/{image_id}/overlay", summary="分析标注图")
def get_overlay(image_id: int, session: Session = Depends(get_session),
                storage: Storage = Depends(get_storage)) -> FileResponse:
    image = get_image_or_404(session, image_id)
    return _file_response(storage, image.overlay_path)


@router.post("/{image_id}/reanalyze", response_model=ImageSummary, summary="重新分析",
             dependencies=[Depends(require_api_key)])
def reanalyze_image(
    image_id: int,
    session: Session = Depends(get_session),
    worker: AnalysisWorker = Depends(get_worker),
) -> ImageSummary:
    image = get_image_or_404(session, image_id)
    image.status = ImageStatus.PENDING
    image.error = None
    session.commit()
    worker.submit([image.id])
    session.refresh(image)
    return image_summary(image)


@router.delete("/{image_id}", status_code=204, summary="删除照片", dependencies=[Depends(require_api_key)])
def delete_image(
    image_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> Response:
    image = get_image_or_404(session, image_id)
    paths = (image.stored_path, image.thumbnail_path, image.overlay_path)
    session.delete(image)
    session.commit()
    storage.delete(*paths)
    return Response(status_code=204)
