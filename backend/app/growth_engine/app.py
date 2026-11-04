"""Standalone FastAPI application entrypoint for SoloEstate AI Growth Engine-lite.
Mounts core SoloEstate routers alongside the growth engine router for isolated testing and execution
without mutating backend/app/main.py.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import psycopg

from app.config import Settings
from app.crm import CRMError
from app.dashboard_api import router as dashboard_router
from app.crm_api import router as crm_router
from app.chat_api import router as chat_router
from app.owner_api import router as owner_router
from app.growth_engine.router import router as growth_engine_router


def create_growth_app() -> FastAPI:
    settings = Settings()
    app = FastAPI(title="SoloEstate AI — Growth Engine Workspace", version="0.1.0")

    # Core routers
    app.include_router(owner_router)
    app.include_router(dashboard_router)
    app.include_router(crm_router)
    app.include_router(chat_router)

    # Growth Engine isolated router
    app.include_router(growth_engine_router)

    @app.get("/growth-engine/health")
    def growth_health():
        return {
            "status": "ok",
            "module": "growth_engine_lite",
            "features": [
                "owner_marketing_brief_voice_transcribe",
                "property_visual_intelligence_sam_3_1",
                "creative_studio_muse_image",
                "marketing_intelligence_muse_spark",
                "workflow_orchestration_muse_wonder",
                "owner_approval_workspace",
            ]
        }

    return app


app = create_growth_app()
