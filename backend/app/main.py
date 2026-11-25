"""FastAPI 应用入口。

启动：在项目根目录执行 ``python run.py``，或 ``uvicorn app.main:app --app-dir backend``。
浏览器访问 http://localhost:8000 打开管理页面，http://localhost:8000/docs 查看接口文档。
"""

from __future__ import annotations

import logging
import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import api_router
from app.config import Settings, get_settings
from app.database import Database
from app.services.processing import AnalysisWorker, build_analyzer
from app.services.storage import Storage

logger = logging.getLogger("app")

API_DESCRIPTION = """
对接巡检无人机照片，自动分析果树的**生长状态**（生长阶段、长势、冠层覆盖度、冠幅）
与**健康状态**（健康评分、黄化、褐变坏死、病斑、冠层稀疏等问题及管理建议）。

- 网页上传：`POST /api/v1/tasks/{task_id}/images`
- 无人机/机场自动推送：`POST /api/v1/drone/ingest`（按任务编号自动归档）
- 即时分析（不保存）：`POST /api/v1/analyze`

配置了 `ORCHARD_API_KEY` 时，所有写操作需在请求头携带 `X-API-Key`。
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.ensure_dirs()
        database = Database(settings.db_url)
        database.create_all()
        storage = Storage(settings.data_dir, settings.max_upload_bytes)
        analyzer = build_analyzer(settings)
        worker = AnalysisWorker(database, storage, analyzer, workers=settings.workers, inline=settings.process_inline)
        app.state.database = database
        app.state.storage = storage
        app.state.analyzer = analyzer
        app.state.worker = worker

        if not settings.api_key:
            logger.warning("未设置 ORCHARD_API_KEY：所有接口无需密钥即可访问，请仅在内网环境使用")
        requeued = worker.requeue_unfinished()
        if requeued:
            logger.info("已重新排队 %d 张未完成分析的照片", requeued)
        logger.info("数据目录：%s，分析器：%s", settings.data_dir, analyzer.description)
        try:
            yield
        finally:
            worker.shutdown()
            database.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=API_DESCRIPTION,
        lifespan=lifespan,
    )
    app.state.settings = settings

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(api_router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if settings.frontend_dir.is_dir():
        # 部分 Windows 系统注册表把 .js 映射为 text/plain，会导致浏览器拒绝加载 ES 模块
        mimetypes.add_type("text/javascript", ".js")
        mimetypes.add_type("text/css", ".css")
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    return app


app = create_app()
