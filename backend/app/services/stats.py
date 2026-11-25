"""统计分析：任务汇总、总览看板、地图点位。"""

from __future__ import annotations

from collections import Counter
from datetime import date

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session, joinedload

from app.analysis import knowledge as kb
from app.models import FruitTree, ImageStatus, InspectionImage, InspectionTask
from app.schemas import CountItem, MapData, MapPoint, OverviewStats, TaskStats, TreeMapPoint, TrendPoint
from app.services.presenters import image_summary
from app.services.trees import tree_status

ABNORMAL_LEVELS = ("mild", "moderate", "severe")
ALERT_LEVELS = ("moderate", "severe")


def task_stats(session: Session, task_ids: list[int]) -> dict[int, TaskStats]:
    stats = {tid: TaskStats() for tid in task_ids}
    if not task_ids:
        return stats
    img = InspectionImage
    rows = session.execute(
        select(
            img.task_id,
            func.count(),
            func.sum(case((img.status == ImageStatus.DONE, 1), else_=0)),
            func.sum(case((img.status.in_(ImageStatus.UNFINISHED), 1), else_=0)),
            func.sum(case((img.status == ImageStatus.FAILED, 1), else_=0)),
            func.sum(case((img.health_level.in_(ABNORMAL_LEVELS), 1), else_=0)),
            func.avg(img.health_score),
        )
        .where(img.task_id.in_(task_ids))
        .group_by(img.task_id)
    ).all()
    for tid, total, done, pending, failed, abnormal, avg in rows:
        s = stats[tid]
        s.image_count, s.analyzed_count, s.pending_count = total, done or 0, pending or 0
        s.failed_count, s.abnormal_count = failed or 0, abnormal or 0
        s.avg_health_score = round(float(avg), 1) if avg is not None else None
    levels = session.execute(
        select(img.task_id, img.health_level, func.count())
        .where(img.task_id.in_(task_ids), img.health_level.is_not(None))
        .group_by(img.task_id, img.health_level)
    ).all()
    for tid, level, count in levels:
        stats[tid].level_counts[level] = count
    return stats


def _count_items(counter: dict[str, int], labels: dict[str, str], keep_order: bool = True) -> list[CountItem]:
    codes = list(labels) if keep_order else sorted(counter, key=lambda c: counter[c], reverse=True)
    return [CountItem(code=c, label=labels.get(c, c), count=counter.get(c, 0)) for c in codes if counter.get(c)]


def overview(session: Session, task_id: int | None = None, days: int = 30) -> OverviewStats:
    img = InspectionImage

    def scoped(stmt: Select) -> Select:
        return stmt.where(img.task_id == task_id) if task_id is not None else stmt

    status_counts = dict(session.execute(scoped(select(img.status, func.count()).group_by(img.status))).all())
    level_counts = dict(
        session.execute(
            scoped(select(img.health_level, func.count()).where(img.health_level.is_not(None)).group_by(img.health_level))
        ).all()
    )
    stage_counts = dict(
        session.execute(
            scoped(select(img.growth_stage, func.count()).where(img.growth_stage.is_not(None)).group_by(img.growth_stage))
        ).all()
    )
    vigor_counts = dict(
        session.execute(
            scoped(select(img.vigor_level, func.count()).where(img.vigor_level.is_not(None)).group_by(img.vigor_level))
        ).all()
    )
    avg_score = session.scalar(scoped(select(func.avg(img.health_score))))

    issue_counter: Counter[str] = Counter()
    for codes in session.scalars(scoped(select(img.issue_codes).where(img.issue_codes.is_not(None)))):
        issue_counter.update(c for c in codes.split(",") if c)
    issue_labels = {code: (kb.ISSUES[code].name if code in kb.ISSUES else code) for code in issue_counter}

    # 健康趋势：按拍摄日期（无则按上传日期）汇总最近 days 个巡检日
    observed = func.coalesce(img.captured_at, img.uploaded_at)
    day = func.date(observed)
    trend_rows = session.execute(
        scoped(
            select(
                day,
                func.count(),
                func.avg(img.health_score),
                func.sum(case((img.health_level.in_(ABNORMAL_LEVELS), 1), else_=0)),
            )
            .where(img.status == ImageStatus.DONE)
            .group_by(day)
            .order_by(day.desc())
            .limit(days)
        )
    ).all()[::-1]
    trend = [
        TrendPoint(
            date=d if isinstance(d, date) else date.fromisoformat(str(d)),
            image_count=count,
            avg_health_score=round(float(avg), 1) if avg is not None else None,
            abnormal_count=abnormal or 0,
        )
        for d, count, avg, abnormal in trend_rows
    ]

    alerts = session.scalars(
        scoped(
            select(img)
            .options(joinedload(img.task), joinedload(img.tree))
            .where(img.health_level.in_(ALERT_LEVELS))
            .order_by(img.health_score.asc(), img.analyzed_at.desc())
            .limit(8)
        )
    ).all()

    task_count = 1 if task_id is not None else session.scalar(select(func.count()).select_from(InspectionTask))
    return OverviewStats(
        task_count=task_count or 0,
        tree_count=session.scalar(select(func.count()).select_from(FruitTree)) or 0,
        image_count=sum(status_counts.values()),
        analyzed_count=status_counts.get(ImageStatus.DONE, 0),
        pending_count=sum(status_counts.get(s, 0) for s in ImageStatus.UNFINISHED),
        failed_count=status_counts.get(ImageStatus.FAILED, 0),
        abnormal_count=sum(level_counts.get(level, 0) for level in ABNORMAL_LEVELS),
        avg_health_score=round(float(avg_score), 1) if avg_score is not None else None,
        health_levels=_count_items(level_counts, kb.HEALTH_LEVELS),
        growth_stages=_count_items(stage_counts, kb.GROWTH_STAGES),
        vigor_levels=_count_items(vigor_counts, kb.VIGOR_LEVELS),
        top_issues=_count_items(dict(issue_counter), issue_labels, keep_order=False)[:8],
        trend=trend,
        recent_alerts=[image_summary(i) for i in alerts],
    )


def map_data(session: Session, task_id: int | None = None, limit: int = 5000) -> MapData:
    img = InspectionImage
    stmt = select(img).where(img.latitude.is_not(None), img.longitude.is_not(None))
    if task_id is not None:
        stmt = stmt.where(img.task_id == task_id)
    images = session.scalars(stmt.order_by(img.id.desc()).limit(limit)).all()
    points = [
        MapPoint(
            image_id=i.id,
            task_id=i.task_id,
            latitude=i.ground_latitude if i.ground_latitude is not None else i.latitude,
            longitude=i.ground_longitude if i.ground_longitude is not None else i.longitude,
            health_score=i.health_score,
            health_level=i.health_level,
            health_level_label=kb.HEALTH_LEVELS.get(i.health_level or ""),
            status=i.status,
            original_filename=i.original_filename,
            thumbnail_url=f"/api/v1/images/{i.id}/thumbnail",
        )
        for i in images
    ]

    trees_stmt = select(FruitTree).where(FruitTree.latitude.is_not(None), FruitTree.longitude.is_not(None))
    if task_id is not None:
        tree_ids = select(img.tree_id).where(img.task_id == task_id, img.tree_id.is_not(None))
        trees_stmt = trees_stmt.where(FruitTree.id.in_(tree_ids))
    trees = session.scalars(trees_stmt.limit(limit)).all()
    status = tree_status(session, [t.id for t in trees])
    tree_points = [
        TreeMapPoint(
            tree_id=t.id,
            code=t.code,
            latitude=t.latitude,
            longitude=t.longitude,
            last_health_level=status[t.id].last_image.health_level if status[t.id].last_image else None,
        )
        for t in trees
    ]
    return MapData(images=points, trees=tree_points)
