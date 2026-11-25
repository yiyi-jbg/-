"""接口请求/响应数据结构。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.analysis.result import AnalysisResult

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


def _strip(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


# ---------------------------------------------------------------------------
# 巡检任务
# ---------------------------------------------------------------------------


class TaskBase(BaseModel):
    mission_code: str | None = Field(default=None, max_length=120, description="无人机/机场侧任务编号")
    orchard: str | None = Field(default=None, max_length=120, description="果园名称")
    plot: str | None = Field(default=None, max_length=120, description="地块/片区")
    fruit_type: str | None = Field(default=None, max_length=60, description="果树种类")
    drone_model: str | None = Field(default=None, max_length=120)
    drone_sn: str | None = Field(default=None, max_length=120)
    operator: str | None = Field(default=None, max_length=60)
    flight_date: date | None = None
    flight_height_m: float | None = Field(default=None, gt=0, le=1000, description="飞行相对高度（米）")
    notes: str | None = Field(default=None, max_length=2000)

    _strip_strings = field_validator(
        "mission_code", "orchard", "plot", "fruit_type", "drone_model", "drone_sn", "operator", "notes",
        mode="before",
    )(_strip)


class TaskCreate(TaskBase):
    name: str = Field(min_length=1, max_length=200, description="任务名称")


class TaskUpdate(TaskBase):
    name: str | None = Field(default=None, min_length=1, max_length=200)


class TaskStats(BaseModel):
    image_count: int = 0
    analyzed_count: int = 0
    pending_count: int = 0
    failed_count: int = 0
    abnormal_count: int = 0
    avg_health_score: float | None = None
    level_counts: dict[str, int] = {}


class TaskOut(TaskBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    source: str
    created_at: datetime
    stats: TaskStats = TaskStats()


# ---------------------------------------------------------------------------
# 巡检照片
# ---------------------------------------------------------------------------


class ImageSummary(BaseModel):
    id: int
    task_id: int
    task_name: str | None = None
    tree_id: int | None = None
    tree_code: str | None = None
    original_filename: str
    status: str
    error: str | None = None
    uploaded_at: datetime
    captured_at: datetime | None = None
    analyzed_at: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    altitude_rel: float | None = None
    health_score: float | None = None
    health_level: str | None = None
    health_level_label: str | None = None
    growth_stage: str | None = None
    growth_stage_label: str | None = None
    vigor_level: str | None = None
    vigor_label: str | None = None
    primary_issue: str | None = None
    image_url: str
    thumbnail_url: str
    overlay_url: str | None = None


class ImageDetail(ImageSummary):
    width: int | None = None
    height: int | None = None
    file_size: int
    image_format: str | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    focal_length_mm: float | None = None
    focal_length_35mm: float | None = None
    gimbal_pitch: float | None = None
    gimbal_yaw: float | None = None
    flight_yaw: float | None = None
    altitude_abs: float | None = None
    ground_latitude: float | None = None
    ground_longitude: float | None = None
    gsd_cm: float | None = None
    tree_distance_m: float | None = None
    metadata: dict[str, Any] = {}
    result: AnalysisResult | None = None


class UploadItem(BaseModel):
    filename: str
    status: Literal["accepted", "duplicate", "rejected"]
    image_id: int | None = None
    error: str | None = None


class UploadResponse(BaseModel):
    task_id: int
    accepted: int
    duplicates: int
    rejected: int
    items: list[UploadItem]


class DroneIngestResponse(UploadResponse):
    mission_code: str
    task_created: bool


class MissionResult(BaseModel):
    """供无人机平台按任务编号回查结果。"""

    task: TaskOut
    images: list[ImageSummary]


# ---------------------------------------------------------------------------
# 果树档案
# ---------------------------------------------------------------------------


class TreeBase(BaseModel):
    orchard: str | None = Field(default=None, max_length=120)
    plot: str | None = Field(default=None, max_length=120)
    variety: str | None = Field(default=None, max_length=120, description="品种")
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    planted_year: int | None = Field(default=None, ge=1900, le=2100)
    notes: str | None = Field(default=None, max_length=2000)

    _strip_strings = field_validator("orchard", "plot", "variety", "notes", mode="before")(_strip)


class TreeCreate(TreeBase):
    code: str = Field(min_length=1, max_length=60, description="果树编号")

    _strip_code = field_validator("code", mode="before")(lambda v: v.strip() if isinstance(v, str) else v)


class TreeUpdate(TreeBase):
    code: str | None = Field(default=None, min_length=1, max_length=60)


class TreeOut(TreeBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    created_at: datetime
    image_count: int = 0
    last_inspected_at: datetime | None = None
    last_health_score: float | None = None
    last_health_level: str | None = None
    last_health_level_label: str | None = None
    last_growth_stage_label: str | None = None
    last_image_id: int | None = None


class TreeHistoryPoint(BaseModel):
    image_id: int
    task_id: int
    observed_at: datetime
    health_score: float | None
    health_level: str | None
    health_level_label: str | None
    growth_stage_label: str | None
    vigor_label: str | None
    primary_issue: str | None
    thumbnail_url: str


class TreeDetail(TreeOut):
    history: list[TreeHistoryPoint] = []


class TreeImportResult(BaseModel):
    created: int
    updated: int
    errors: list[str]
    matched_images: int


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------


class CountItem(BaseModel):
    code: str
    label: str
    count: int


class TrendPoint(BaseModel):
    date: date
    image_count: int
    avg_health_score: float | None
    abnormal_count: int


class OverviewStats(BaseModel):
    task_count: int
    tree_count: int
    image_count: int
    analyzed_count: int
    pending_count: int
    failed_count: int
    abnormal_count: int
    avg_health_score: float | None
    health_levels: list[CountItem]
    growth_stages: list[CountItem]
    vigor_levels: list[CountItem]
    top_issues: list[CountItem]
    trend: list[TrendPoint]
    recent_alerts: list[ImageSummary]


class MapPoint(BaseModel):
    image_id: int
    task_id: int
    latitude: float
    longitude: float
    health_score: float | None
    health_level: str | None
    health_level_label: str | None
    status: str
    original_filename: str
    thumbnail_url: str


class TreeMapPoint(BaseModel):
    tree_id: int
    code: str
    latitude: float
    longitude: float
    last_health_level: str | None


class MapData(BaseModel):
    images: list[MapPoint]
    trees: list[TreeMapPoint]


class SystemInfo(BaseModel):
    app_name: str
    version: str
    analyzer: str
    model_loaded: bool
    api_key_required: bool
    max_upload_mb: int
    tree_match_radius_m: float
    workers: int
    enums: dict[str, dict[str, str]]


class AnalyzeResponse(BaseModel):
    metadata: dict[str, Any]
    result: AnalysisResult
    overlay_base64: str | None = Field(default=None, description="标注图（JPEG，Base64 编码）")
