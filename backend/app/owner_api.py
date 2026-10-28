from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from psycopg.rows import dict_row

from app.agent.orchestrator import AgentRunner
from app.agent.schemas import RunRequest
from app.agent.store import RunStore
from app.config import Settings
from app.database import connect
from app.owner import OwnerService, DecisionRequest, authenticate_owner, payload_hash
from app.services.meta_model import MetaModelService
from app.dashboard_sessions import DashboardSessions

bearer = HTTPBearer(auto_error=False)


def owner_settings():
    return Settings()


def require_owner(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), settings: Settings = Depends(owner_settings)):
    try:
        token=credentials.credentials if credentials else None
        if token and token.startswith("sess_"):
            DashboardSessions().verify(token)
        else:
            authenticate_owner(token,settings)
    except PermissionError:
        raise HTTPException(status_code=401, detail="Valid owner credentials are required.", headers={"WWW-Authenticate": "Bearer"}) from None
    return "owner"


def owner_service():
    return OwnerService()


router = APIRouter(prefix="/owner", dependencies=[Depends(require_owner)])


@router.post("/dashboard/tickets")
def ticket():
    return DashboardSessions().issue()


@router.get("/approvals/{approval_id}")
def review(approval_id: UUID, service: OwnerService = Depends(owner_service)):
    return service.review(approval_id)


@router.get("/approvals")
def pending():
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute("SELECT a.id,a.run_id,a.kind,a.payload FROM soloestate.approvals a JOIN soloestate.runs r ON r.id=a.run_id WHERE a.status='pending' AND r.origin='agent' ORDER BY a.created_at,a.id LIMIT 101").fetchall()
    return {"approvals": [{**row,"payload_hash":payload_hash(row["payload"])} for row in rows[:100]], "truncated":len(rows)>100}


@router.post("/approvals/{approval_id}/decision")
def decide(approval_id: UUID, request: DecisionRequest, service: OwnerService = Depends(owner_service)):
    return service.decide(approval_id, request)


@router.get("/runs/{run_id}")
def status(run_id: UUID):
    return RunStore().status(run_id)


@router.get("/runs")
def runs(request_key: str | None = Query(default=None,min_length=1,max_length=128)):
    if request_key is not None:
        return RunStore().find(request_key)
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            rows=cur.execute("SELECT id,request_key,goal,status,updated_at FROM soloestate.runs WHERE origin='agent' ORDER BY updated_at DESC,id LIMIT 21").fetchall()
    return {"runs":rows[:20],"truncated":len(rows)>20}


@router.post("/runs")
async def start(request: RunRequest):
    settings = Settings()
    async with MetaModelService(settings) as model:
        return await AgentRunner(model, settings=settings).run(request)


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recover: bool = Field(default=False, strict=True)


@router.post("/runs/{run_id}/resume")
async def resume(run_id: UUID, request: ResumeRequest):
    settings = Settings()
    async with MetaModelService(settings) as model:
        return await AgentRunner(model, settings=settings).resume(run_id, recover=request.recover)
