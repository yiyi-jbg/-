"""无人机对接接口：机场/地面站/同步脚本按任务编号推送照片，并按编号回查结果。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.api.deps import get_session, get_settings, get_storage, get_worker, require_api_key
from app.api.tasks import upload_response
from app.config import Settings
from app.models import InspectionImage, InspectionTask
from app.schemas import DroneIngestResponse, MissionResult
from app.services.ingest import IncomingFile, ingest_files
from app.services.presenters import image_summary, task_out
from app.services.processing import AnalysisWorker
from app.services.stats import task_stats
from app.services.storage import Storage

router = APIRouter(prefix="/drone", tags=["无人机对接"])


def _get_or_create_task(session: Session, mission_code: str, defaults: dict) -> tuple[InspectionTask, bool]:
    task = session.scalar(select(InspectionTask).where(InspectionTask.mission_code == mission_code))
    if task is not None:
        # 补全首次推送时缺失的信息
        for key, value in defaults.items():
            if value and not getattr(task, key):
                setattr(task, key, value)
        session.commit()
        return task, False
    task = InspectionTask(mission_code=mission_code, source="drone", **defaults)
    session.add(task)
    try:
        session.commit()
    except IntegrityError:  # 并发推送同一任务
        session.rollback()
        task = session.scalar(select(InspectionTask).where(InspectionTask.mission_code == mission_code))
        if task is None:
            raise
        return task, False
    return task, True


@router.post(
    "/ingest",
    response_model=DroneIngestResponse,
    summary="无人机照片推送",
    description=(
        "按 mission_code（无人机/机场侧的任务编号）自动归档照片：任务不存在时自动创建。"
        "同一任务内重复推送同一张照片会被识别为重复，不会重复分析，因此推送失败时可放心重试。"
    ),
    dependencies=[Depends(require_api_key)],
)
def ingest(
    mission_code: str = Form(..., min_length=1, max_length=120, description="任务编号，如航线名或飞行架次号"),
    files: list[UploadFile] = File(..., description="无人机照片"),
    task_name: str | None = Form(None, max_length=200),
    orchard: str | None = Form(None, max_length=120),
    plot: str | None = Form(None, max_length=120),
    fruit_type: str | None = Form(None, max_length=60),
    drone_model: str | None = Form(None, max_length=120),
    drone_sn: str | None = Form(None, max_length=120),
    operator: str | None = Form(None, max_length=60),
    flight_height_m: float | None = Form(None, gt=0, le=1000),
    session: Session = Depends(get_session),
    storage: Storage = Depends(get_storage),
    settings: Settings = Depends(get_settings),
    worker: AnalysisWorker = Depends(get_worker),
) -> DroneIngestResponse:
    code = mission_code.strip()
    if not code:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="mission_code 不能为空")
    defaults = {
        "name": (task_name or "").strip() or f"无人机巡检 {code}",
        "orchard": orchard,
        "plot": plot,
        "fruit_type": fruit_type,
        "drone_model": drone_model,
        "drone_sn": drone_sn,
        "operator": operator,
        "flight_height_m": flight_height_m,
    }
    task, created = _get_or_create_task(session, code, defaults)
    items, accepted = ingest_files(
        session, storage, settings, task, [IncomingFile(f.filename or "image.jpg", f.file) for f in files]
    )
    if task.flight_date is None:
        first = session.scalar(
            select(InspectionImage.captured_at)
            .where(InspectionImage.task_id == task.id, InspectionImage.captured_at.is_not(None))
            .order_by(InspectionImage.captured_at)
            .limit(1)
        )
        if first:
            task.flight_date = first.date()
            session.commit()
    worker.submit(accepted)
    base = upload_response(task.id, items)
    return DroneIngestResponse(**base.model_dump(), mission_code=code, task_created=created)


@router.get("/missions/{mission_code}", response_model=MissionResult, summary="按任务编号查询分析结果")
def mission_result(mission_code: str, session: Session = Depends(get_session)) -> MissionResult:
    task = session.scalar(select(InspectionTask).where(InspectionTask.mission_code == mission_code))
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="任务编号不存在")
    images = session.scalars(
        select(InspectionImage)
        .options(joinedload(InspectionImage.task), joinedload(InspectionImage.tree))
        .where(InspectionImage.task_id == task.id)
        .order_by(InspectionImage.captured_at, InspectionImage.id)
    ).all()
    return MissionResult(
        task=task_out(task, task_stats(session, [task.id])[task.id]),
        images=[image_summary(i) for i in images],
    )
