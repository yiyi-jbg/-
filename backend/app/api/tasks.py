"""巡检任务接口。"""

from __future__ import annotations

import csv
import io
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.analysis import knowledge as kb
from app.api.deps import get_session, get_settings, get_storage, get_worker, require_api_key
from app.config import Settings
from app.models import ImageStatus, InspectionImage, InspectionTask
from app.schemas import Page, TaskCreate, TaskOut, TaskUpdate, UploadResponse
from app.services.ingest import IncomingFile, ingest_files
from app.services.presenters import task_out
from app.services.processing import AnalysisWorker
from app.services.stats import task_stats
from app.services.storage import Storage

router = APIRouter(prefix="/tasks", tags=["巡检任务"])


def get_task_or_404(session: Session, task_id: int) -> InspectionTask:
    task = session.get(InspectionTask, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="巡检任务不存在")
    return task


def upload_response(task_id: int, items: list) -> UploadResponse:
    return UploadResponse(
        task_id=task_id,
        accepted=sum(i.status == "accepted" for i in items),
        duplicates=sum(i.status == "duplicate" for i in items),
        rejected=sum(i.status == "rejected" for i in items),
        items=items,
    )


@router.get("", response_model=Page[TaskOut], summary="巡检任务列表")
def list_tasks(
    q: str | None = Query(None, description="按名称/果园/地块/任务编号搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    session: Session = Depends(get_session),
) -> Page[TaskOut]:
    stmt = select(InspectionTask)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                InspectionTask.name.ilike(like),
                InspectionTask.orchard.ilike(like),
                InspectionTask.plot.ilike(like),
                InspectionTask.mission_code.ilike(like),
            )
        )
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    tasks = session.scalars(
        stmt.order_by(InspectionTask.created_at.desc(), InspectionTask.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    stats = task_stats(session, [t.id for t in tasks])
    return Page(items=[task_out(t, stats[t.id]) for t in tasks], total=total, page=page, page_size=page_size)


@router.post("", response_model=TaskOut, status_code=201, summary="创建巡检任务",
             dependencies=[Depends(require_api_key)])
def create_task(body: TaskCreate, session: Session = Depends(get_session)) -> TaskOut:
    task = InspectionTask(**body.model_dump(), source="web")
    session.add(task)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="任务编号已存在") from None
    return task_out(task, task_stats(session, [task.id])[task.id])


@router.get("/{task_id}", response_model=TaskOut, summary="巡检任务详情")
def get_task(task_id: int, session: Session = Depends(get_session)) -> TaskOut:
    task = get_task_or_404(session, task_id)
    return task_out(task, task_stats(session, [task.id])[task.id])


@router.patch("/{task_id}", response_model=TaskOut, summary="修改巡检任务", dependencies=[Depends(require_api_key)])
def update_task(task_id: int, body: TaskUpdate, session: Session = Depends(get_session)) -> TaskOut:
    task = get_task_or_404(session, task_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        if key == "name" and value is None:
            continue
        setattr(task, key, value)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="任务编号已存在") from None
    return task_out(task, task_stats(session, [task.id])[task.id])


@router.delete("/{task_id}", status_code=204, summary="删除巡检任务（含全部照片）",
               dependencies=[Depends(require_api_key)])
def delete_task(
    task_id: int,
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
) -> Response:
    task = get_task_or_404(session, task_id)
    session.delete(task)
    session.commit()
    storage.delete_task_files(task_id)
    return Response(status_code=204)


@router.post("/{task_id}/images", response_model=UploadResponse, summary="上传巡检照片（可多张）",
             dependencies=[Depends(require_api_key)])
def upload_images(
    task_id: int,
    files: list[UploadFile] = File(..., description="无人机照片，支持 JPG/PNG/TIFF"),
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
    worker: AnalysisWorker = Depends(get_worker),
) -> UploadResponse:
    task = get_task_or_404(session, task_id)
    items, accepted = ingest_files(
        session, storage, settings, task, [IncomingFile(f.filename or "image.jpg", f.file) for f in files]
    )
    worker.submit(accepted)
    return upload_response(task.id, items)


@router.post("/{task_id}/reanalyze", summary="重新分析任务内全部照片", dependencies=[Depends(require_api_key)])
def reanalyze_task(
    task_id: int,
    session: Session = Depends(get_session),
    worker: AnalysisWorker = Depends(get_worker),
) -> dict[str, int]:
    get_task_or_404(session, task_id)
    ids = list(session.scalars(select(InspectionImage.id).where(InspectionImage.task_id == task_id)))
    session.execute(
        update(InspectionImage).where(InspectionImage.task_id == task_id).values(status=ImageStatus.PENDING, error=None)
    )
    session.commit()
    worker.submit(ids)
    return {"queued": len(ids)}


EXPORT_COLUMNS = [
    "照片ID", "文件名", "拍摄时间", "纬度", "经度", "相对高度(m)", "关联果树", "分析状态",
    "健康评分", "健康等级", "生长阶段", "长势", "主要问题", "全部问题",
]


@router.get("/{task_id}/export.csv", summary="导出任务分析结果（CSV，可用 Excel 打开）")
def export_task(task_id: int, session: Session = Depends(get_session)) -> Response:
    task = get_task_or_404(session, task_id)
    images = session.scalars(
        select(InspectionImage)
        .options(joinedload(InspectionImage.tree))
        .where(InspectionImage.task_id == task_id)
        .order_by(InspectionImage.captured_at, InspectionImage.id)
    ).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(EXPORT_COLUMNS)
    status_labels = {"pending": "待分析", "processing": "分析中", "done": "已完成", "failed": "失败"}
    for img in images:
        issues = [kb.ISSUES[c].name if c in kb.ISSUES else c for c in (img.issue_codes or "").split(",") if c]
        writer.writerow([
            img.id,
            img.original_filename,
            img.captured_at.isoformat(sep=" ") if img.captured_at else "",
            f"{img.latitude:.7f}" if img.latitude is not None else "",
            f"{img.longitude:.7f}" if img.longitude is not None else "",
            f"{img.altitude_rel:.1f}" if img.altitude_rel is not None else "",
            img.tree.code if img.tree else "",
            status_labels.get(img.status, img.status),
            f"{img.health_score:.1f}" if img.health_score is not None else "",
            kb.HEALTH_LEVELS.get(img.health_level or "", ""),
            kb.GROWTH_STAGES.get(img.growth_stage or "", ""),
            kb.VIGOR_LEVELS.get(img.vigor_level or "", ""),
            img.primary_issue or "",
            "；".join(issues),
        ])
    filename = quote(f"{task.name}-分析结果.csv")
    return Response(
        content=buf.getvalue().encode("utf-8-sig"),  # 带 BOM，Excel 打开不乱码
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
