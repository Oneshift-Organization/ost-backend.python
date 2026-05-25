from fastapi import APIRouter

from app.api.v1.routes import analysis, billing, health, reports

api_router = APIRouter(prefix="/v1")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(billing.router, tags=["billing"])
api_router.include_router(analysis.router, tags=["analyses"])
api_router.include_router(reports.router, tags=["reports"])
