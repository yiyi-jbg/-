"""接口公共依赖：数据库会话、配置、存储、分析队列、接口密钥校验。"""

from __future__ import annotations

import secrets
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader
from sqlalchemy.orm import Session

from app.config import Settings
from app.services.processing import AnalysisWorker
from app.services.storage import Storage

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, description="写操作需要的接口密钥（未配置时可省略）")


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.database.session() as session:
        yield session


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_worker(request: Request) -> AnalysisWorker:
    return request.app.state.worker


def require_api_key(
    settings: Settings = Depends(get_settings),
    api_key: str | None = Depends(_api_key_header),
) -> None:
    """配置了 ORCHARD_API_KEY 时，校验请求头 X-API-Key。"""
    if not settings.api_key:
        return
    if not api_key or not secrets.compare_digest(api_key, settings.api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="接口密钥无效，请在请求头 X-API-Key 中提供正确的密钥")
