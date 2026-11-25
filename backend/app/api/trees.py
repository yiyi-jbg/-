"""果树档案接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_session, get_settings, require_api_key
from app.config import Settings
from app.models import FruitTree, ImageStatus, InspectionImage
from app.schemas import Page, TreeCreate, TreeDetail, TreeImportResult, TreeOut, TreeUpdate
from app.services.presenters import tree_history_point, tree_out
from app.services.trees import parse_tree_csv, rematch_all, tree_status, upsert_trees

router = APIRouter(prefix="/trees", tags=["果树档案"])

MAX_CSV_BYTES = 5 * 1024 * 1024


def get_tree_or_404(session: Session, tree_id: int) -> FruitTree:
    tree = session.get(FruitTree, tree_id)
    if tree is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="果树不存在")
    return tree


@router.get("", response_model=Page[TreeOut], summary="果树列表（含最近一次巡检状态）")
def list_trees(
    q: str | None = Query(None, description="按编号/品种/果园/地块搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> Page[TreeOut]:
    stmt = select(FruitTree)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(FruitTree.code.ilike(like), FruitTree.variety.ilike(like), FruitTree.orchard.ilike(like),
                FruitTree.plot.ilike(like))
        )
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    trees = session.scalars(stmt.order_by(FruitTree.code).offset((page - 1) * page_size).limit(page_size)).all()
    status_map = tree_status(session, [t.id for t in trees])
    return Page(items=[tree_out(t, status_map[t.id]) for t in trees], total=total, page=page, page_size=page_size)


@router.post("", response_model=TreeOut, status_code=201, summary="登记果树", dependencies=[Depends(require_api_key)])
def create_tree(
    body: TreeCreate,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TreeOut:
    tree = FruitTree(**body.model_dump())
    session.add(tree)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="果树编号已存在") from None
    if tree.latitude is not None and tree.longitude is not None:
        rematch_all(session, settings.tree_match_radius_m)
    return tree_out(tree, tree_status(session, [tree.id])[tree.id])


@router.post("/import", response_model=TreeImportResult, summary="CSV 批量导入果树",
             description="CSV 列：编号(code)、纬度(latitude)、经度(longitude)、品种、果园、地块、定植年份、备注。"
                         "编号已存在时更新该果树。支持 UTF-8 与 GBK 编码。",
             dependencies=[Depends(require_api_key)])
def import_trees(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TreeImportResult:
    content = file.file.read(MAX_CSV_BYTES + 1)
    if len(content) > MAX_CSV_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="CSV 文件不能超过 5MB")
    try:
        records, errors = parse_tree_csv(content)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    created, updated = upsert_trees(session, records) if records else (0, 0)
    matched = rematch_all(session, settings.tree_match_radius_m) if records else 0
    return TreeImportResult(created=created, updated=updated, errors=errors[:50], matched_images=matched)


@router.post("/rematch", summary="重新匹配照片与果树", dependencies=[Depends(require_api_key)])
def rematch(session: Session = Depends(get_session), settings: Settings = Depends(get_settings)) -> dict[str, int]:
    return {"matched_images": rematch_all(session, settings.tree_match_radius_m)}


@router.get("/{tree_id}", response_model=TreeDetail, summary="果树详情与历史巡检记录")
def get_tree(tree_id: int, session: Session = Depends(get_session)) -> TreeDetail:
    tree = get_tree_or_404(session, tree_id)
    status_map = tree_status(session, [tree.id])
    images = session.scalars(
        select(InspectionImage)
        .where(InspectionImage.tree_id == tree.id, InspectionImage.status == ImageStatus.DONE)
        .order_by(func.coalesce(InspectionImage.captured_at, InspectionImage.uploaded_at), InspectionImage.id)
    ).all()
    detail = TreeDetail(**tree_out(tree, status_map[tree.id]).model_dump())
    detail.history = [tree_history_point(i) for i in images]
    return detail


@router.patch("/{tree_id}", response_model=TreeOut, summary="修改果树信息", dependencies=[Depends(require_api_key)])
def update_tree(
    tree_id: int,
    body: TreeUpdate,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TreeOut:
    tree = get_tree_or_404(session, tree_id)
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        if key == "code" and value is None:
            continue
        setattr(tree, key, value)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="果树编号已存在") from None
    if "latitude" in changes or "longitude" in changes:
        rematch_all(session, settings.tree_match_radius_m)
    return tree_out(tree, tree_status(session, [tree.id])[tree.id])


@router.delete("/{tree_id}", status_code=204, summary="删除果树（照片保留）", dependencies=[Depends(require_api_key)])
def delete_tree(tree_id: int, session: Session = Depends(get_session)) -> Response:
    tree = get_tree_or_404(session, tree_id)
    session.delete(tree)
    session.commit()
    return Response(status_code=204)
