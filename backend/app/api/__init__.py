"""HTTP 接口（统一前缀 /api/v1）。"""

from fastapi import APIRouter

from app.api import drone, images, system, tasks, trees

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(tasks.router)
api_router.include_router(images.router)
api_router.include_router(drone.router)
api_router.include_router(trees.router)
api_router.include_router(system.router)

__all__ = ["api_router"]
