from fastapi import FastAPI

from app.api.v1.routes.health import router as health_router

app = FastAPI(title="OneShift Backend", version="0.0.1")

app.include_router(health_router)
