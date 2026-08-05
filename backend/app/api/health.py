"""健康检查子路由。"""
from fastapi import APIRouter

from app.services.health import get_health_status


router = APIRouter()


router.add_api_route(
    "/health",
    get_health_status,
    methods=["GET"],
    tags=["health"],
)