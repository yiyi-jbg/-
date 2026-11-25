"""数据库模型：巡检任务、巡检照片（含分析结果）、果树档案。"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def now() -> datetime:
    """统一使用服务器本地时间（与无人机照片中的拍摄时间保持一致，均不带时区）。"""
    return datetime.now().replace(microsecond=0)


class ImageStatus:
    PENDING = "pending"  # 已上传，等待分析
    PROCESSING = "processing"  # 分析中
    DONE = "done"  # 分析完成
    FAILED = "failed"  # 分析失败

    UNFINISHED = (PENDING, PROCESSING)


class InspectionTask(Base):
    """巡检任务：通常对应无人机的一次飞行/一条航线。"""

    __tablename__ = "inspection_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # 无人机/机场侧的任务编号，无人机自动推送时按此编号归档
    mission_code: Mapped[str | None] = mapped_column(String(120), unique=True, index=True)
    orchard: Mapped[str | None] = mapped_column(String(120))  # 果园
    plot: Mapped[str | None] = mapped_column(String(120))  # 地块/片区
    fruit_type: Mapped[str | None] = mapped_column(String(60))  # 果树种类，如苹果、柑橘
    drone_model: Mapped[str | None] = mapped_column(String(120))
    drone_sn: Mapped[str | None] = mapped_column(String(120))
    operator: Mapped[str | None] = mapped_column(String(60))
    flight_date: Mapped[date | None] = mapped_column(Date)
    # 照片中没有相对高度信息时，用该飞行高度估算地面分辨率
    flight_height_m: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), default="web")  # web / drone
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    images: Mapped[list[InspectionImage]] = relationship(
        back_populates="task", cascade="all, delete-orphan", passive_deletes=True
    )


class FruitTree(Base):
    """果树档案：登记每棵树的位置后，带 GPS 的巡检照片会自动关联到最近的果树。"""

    __tablename__ = "fruit_trees"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True, index=True)  # 树编号，如 A-03-015
    orchard: Mapped[str | None] = mapped_column(String(120))
    plot: Mapped[str | None] = mapped_column(String(120))
    variety: Mapped[str | None] = mapped_column(String(120))  # 品种，如 红富士
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    planted_year: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    images: Mapped[list[InspectionImage]] = relationship(back_populates="tree", passive_deletes=True)


class InspectionImage(Base):
    """巡检照片，同时保存最新一次分析结果。"""

    __tablename__ = "inspection_images"
    __table_args__ = (UniqueConstraint("task_id", "sha256", name="uq_image_task_sha256"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("inspection_tasks.id", ondelete="CASCADE"), index=True)
    tree_id: Mapped[int | None] = mapped_column(ForeignKey("fruit_trees.id", ondelete="SET NULL"), index=True)
    tree_distance_m: Mapped[float | None] = mapped_column(Float)

    original_filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(500))  # 相对 data_dir 的路径
    thumbnail_path: Mapped[str | None] = mapped_column(String(500))
    overlay_path: Mapped[str | None] = mapped_column(String(500))
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    image_format: Mapped[str | None] = mapped_column(String(20))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)

    # —— 无人机元数据 ——
    captured_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    altitude_abs: Mapped[float | None] = mapped_column(Float)  # 海拔高度
    altitude_rel: Mapped[float | None] = mapped_column(Float)  # 相对起飞点高度
    ground_latitude: Mapped[float | None] = mapped_column(Float)  # 估算的画面中心地面坐标
    ground_longitude: Mapped[float | None] = mapped_column(Float)
    camera_make: Mapped[str | None] = mapped_column(String(120))
    camera_model: Mapped[str | None] = mapped_column(String(120))
    focal_length_mm: Mapped[float | None] = mapped_column(Float)
    focal_length_35mm: Mapped[float | None] = mapped_column(Float)
    gimbal_pitch: Mapped[float | None] = mapped_column(Float)
    gimbal_yaw: Mapped[float | None] = mapped_column(Float)
    flight_yaw: Mapped[float | None] = mapped_column(Float)
    gsd_cm: Mapped[float | None] = mapped_column(Float)  # 原图地面分辨率（厘米/像素）
    metadata_json: Mapped[str | None] = mapped_column(Text)

    # —— 处理状态 ——
    status: Mapped[str] = mapped_column(String(20), default=ImageStatus.PENDING, index=True)
    error: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime)

    # —— 分析结果摘要（便于列表筛选和统计），完整结果见 result_json ——
    health_score: Mapped[float | None] = mapped_column(Float)
    health_level: Mapped[str | None] = mapped_column(String(20), index=True)
    growth_stage: Mapped[str | None] = mapped_column(String(30))
    vigor_level: Mapped[str | None] = mapped_column(String(20))
    primary_issue: Mapped[str | None] = mapped_column(String(60))
    issue_codes: Mapped[str | None] = mapped_column(String(500))  # 逗号分隔的问题代码
    result_json: Mapped[str | None] = mapped_column(Text)

    task: Mapped[InspectionTask] = relationship(back_populates="images")
    tree: Mapped[FruitTree | None] = relationship(back_populates="images")

    @property
    def observed_at(self) -> datetime:
        """用于时间排序/趋势统计的时间：优先拍摄时间，其次上传时间。"""
        return self.captured_at or self.uploaded_at
