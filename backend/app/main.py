from fastapi import FastAPI, Request
from pydantic import BaseModel
from fastapi.responses import JSONResponse
import psycopg

from app.config import Settings
from app.agent.store import RunConflict
from app.database import DatabaseConfigurationError
from app.owner_api import router as owner_router
from app.services.meta_model import ModelServiceError
from app.dashboard_api import router as dashboard_router
from app.crm_api import router as crm_router
from app.chat_api import router as chat_router
from app.whatsapp_api import router as whatsapp_router
from app.crm import CRMError
from app.growth_engine.router import router as growth_engine_router
from app.growth_engine.services.approval_service import ApprovalServiceError
from app.growth_engine.services.grounding_service import (
    CRMUnavailableError,
    GroundingValidationError,
    PropertyInactiveError,
    PropertyNotFoundError,
    StaleFactReferenceError,
    StaleGroundingError,
    UnsupportedFactClaimError,
)
from app.growth_engine.store import GrowthStoreError
from app.growth_engine.services.voice_service import VoiceServiceError
from app.growth_engine.services.visual_intelligence_service import VisualIntelligenceError
from app.growth_engine.services.creative_service import CreativeServiceError
from app.growth_engine.services.marketing_intelligence_service import MarketingIntelligenceError


class HealthResponse(BaseModel):
    status: str
    service: str


def create_app() -> FastAPI:
    settings = Settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.include_router(owner_router)
    app.include_router(dashboard_router)
    app.include_router(crm_router)
    app.include_router(chat_router)
    app.include_router(whatsapp_router)
    app.include_router(growth_engine_router)

    @app.exception_handler(CRMError)
    async def crm_error(request: Request, exc: CRMError):
        return JSONResponse(status_code=exc.status, content={'status': 'error', 'message': str(exc)})

    @app.exception_handler(RunConflict)
    async def conflict(request: Request, exc: RunConflict):
        return JSONResponse(status_code=409, content={"status": "error", "code": "conflict", "message": str(exc)})

    # Growth Engine specific exception handlers
    @app.exception_handler(PropertyNotFoundError)
    async def property_not_found(request: Request, exc: PropertyNotFoundError):
        return JSONResponse(status_code=404, content={"status": "error", "code": "property_not_found", "message": str(exc)})

    @app.exception_handler(PropertyInactiveError)
    async def property_inactive(request: Request, exc: PropertyInactiveError):
        return JSONResponse(status_code=422, content={"status": "error", "code": "property_inactive", "message": str(exc)})

    @app.exception_handler(StaleGroundingError)
    async def stale_grounding(request: Request, exc: StaleGroundingError):
        return JSONResponse(status_code=409, content={"status": "error", "code": "stale_grounding", "message": str(exc)})

    @app.exception_handler(GroundingValidationError)
    async def grounding_validation(request: Request, exc: GroundingValidationError):
        return JSONResponse(status_code=422, content={"status": "error", "code": "grounding_violation", "message": str(exc), "discrepancies": exc.discrepancies})

    @app.exception_handler(CRMUnavailableError)
    async def crm_unavailable(request: Request, exc: CRMUnavailableError):
        return JSONResponse(status_code=503, content={"status": "error", "code": exc.code, "message": str(exc)})

    @app.exception_handler(UnsupportedFactClaimError)
    async def unsupported_fact(request: Request, exc: UnsupportedFactClaimError):
        return JSONResponse(status_code=422, content={"status": "error", "code": exc.code, "message": str(exc)})

    @app.exception_handler(StaleFactReferenceError)
    async def stale_fact_ref(request: Request, exc: StaleFactReferenceError):
        return JSONResponse(status_code=409, content={"status": "error", "code": exc.code, "message": str(exc)})

    @app.exception_handler(ApprovalServiceError)
    async def approval_error(request: Request, exc: ApprovalServiceError):
        return JSONResponse(status_code=exc.status_code, content={"status": "error", "code": exc.code, "message": str(exc)})

    @app.exception_handler(GrowthStoreError)
    async def growth_store_error(request: Request, exc: GrowthStoreError):
        return JSONResponse(status_code=exc.status_code, content={"status": "error", "code": exc.code, "message": str(exc)})

    @app.exception_handler(VoiceServiceError)
    async def voice_service_error(request: Request, exc: VoiceServiceError):
        return JSONResponse(status_code=400, content={"status": "error", "code": exc.code, "message": str(exc)})

    @app.exception_handler(VisualIntelligenceError)
    async def visual_service_error(request: Request, exc: VisualIntelligenceError):
        return JSONResponse(status_code=400, content={"status": "error", "code": exc.code, "message": str(exc)})

    @app.exception_handler(CreativeServiceError)
    async def creative_service_error(request: Request, exc: CreativeServiceError):
        return JSONResponse(status_code=400, content={"status": "error", "code": exc.code, "message": str(exc)})

    @app.exception_handler(MarketingIntelligenceError)
    async def marketing_service_error(request: Request, exc: MarketingIntelligenceError):
        return JSONResponse(status_code=400, content={"status": "error", "code": exc.code, "message": str(exc)})

    @app.exception_handler(psycopg.Error)
    async def database_error(request: Request, exc: psycopg.Error):
        return JSONResponse(status_code=503, content={"status": "unconfirmed", "code": "database_error", "message": "Database result could not be confirmed. Inspect the run before retrying."})

    @app.exception_handler(DatabaseConfigurationError)
    async def configuration_error(request: Request, exc: DatabaseConfigurationError):
        return JSONResponse(status_code=503, content={"status": "error", "code": "configuration", "message": "Backend database configuration is unavailable."})

    @app.exception_handler(ModelServiceError)
    async def model_error(request: Request, exc: ModelServiceError):
        return JSONResponse(status_code=503, content={"status": "error", "code": exc.code, "message": str(exc)})

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        # Liveness only. No model or database connectivity is implied.
        return HealthResponse(status="ok", service="soloestate-backend")

    return app


app = create_app()
