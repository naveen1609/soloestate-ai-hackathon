from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import JSONResponse
import psycopg

from app.config import Settings
from app.agent.store import RunConflict
from app.database import DatabaseConfigurationError
from app.owner_api import router as owner_router
from app.services.meta_model import ModelServiceError
from app.dashboard_api import router as dashboard_router


class HealthResponse(BaseModel):
    status: str
    service: str


def create_app() -> FastAPI:
    settings = Settings()
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.include_router(owner_router)
    app.include_router(dashboard_router)

    @app.exception_handler(RunConflict)
    async def conflict(request, exc):
        return JSONResponse(status_code=409, content={"status":"error","code":"conflict","message":str(exc)})

    @app.exception_handler(psycopg.Error)
    async def database_error(request, exc):
        return JSONResponse(status_code=503, content={"status":"unconfirmed","code":"database_error","message":"Database result could not be confirmed. Inspect the run before retrying."})

    @app.exception_handler(DatabaseConfigurationError)
    async def configuration_error(request, exc):
        return JSONResponse(status_code=503, content={"status":"error","code":"configuration","message":"Backend database configuration is unavailable."})

    @app.exception_handler(ModelServiceError)
    async def model_error(request, exc):
        return JSONResponse(status_code=503, content={"status":"error","code":exc.code,"message":str(exc)})

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        # Liveness only. No model or database connectivity is implied.
        return HealthResponse(status="ok", service="soloestate-backend")

    return app


app = create_app()
