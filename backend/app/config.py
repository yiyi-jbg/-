"""系统配置。

所有配置项都可以通过环境变量（统一前缀 ``ORCHARD_``）或项目根目录下的 ``.env`` 文件覆盖，
例如 ``ORCHARD_API_KEY=xxx``、``ORCHARD_WORKERS=4``。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# 仓库根目录（backend/app/config.py -> 上两级）
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ORCHARD_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "智慧果园巡检分析系统"

    # 数据目录：保存数据库、上传的原图、缩略图和分析结果图
    data_dir: Path = PROJECT_ROOT / "data"
    # 数据库连接串，默认使用 data 目录下的 SQLite；也可改为 PostgreSQL/MySQL 等
    database_url: str | None = None
    # 前端静态文件目录；目录不存在时只提供 API
    frontend_dir: Path = PROJECT_ROOT / "frontend"

    # 接口密钥：设置后，所有写操作（上传、删除、修改）都需要在请求头携带 X-API-Key
    api_key: str | None = None
    # 单张照片大小上限（MB）。大疆 2000 万像素照片通常在 8~15MB
    max_upload_mb: int = Field(default=60, ge=1, le=1024)

    # 分析时把照片长边缩放到该尺寸，兼顾速度与精度
    analysis_max_side: int = Field(default=1280, ge=256, le=8192)
    # 后台分析线程数
    workers: int = Field(default=2, ge=1, le=32)
    # 为 True 时在上传请求内同步完成分析（测试或小规模使用）
    process_inline: bool = False

    # 照片与果树档案自动关联的距离阈值（米）
    tree_match_radius_m: float = Field(default=3.0, gt=0, le=100)

    # 可选：外接 ONNX 病害识别模型（见 README“接入自训练模型”）
    model_path: Path | None = None
    model_labels: Path | None = None
    model_min_confidence: float = Field(default=0.5, ge=0, le=1)

    # 允许跨域访问的前端地址，逗号分隔；前后端同源部署时无需设置
    cors_origins: Annotated[list[str], NoDecode] = []

    log_level: str = "INFO"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("api_key", mode="before")
    @classmethod
    def _empty_key_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'orchard.db').as_posix()}"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def ensure_dirs(self) -> None:
        for sub in ("uploads", "thumbnails", "overlays"):
            (self.data_dir / sub).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
