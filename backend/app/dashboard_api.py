from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field

from app.dashboard_sessions import DashboardSessions

router=APIRouter(prefix="/dashboard")
bearer=HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    model_config=ConfigDict(extra="forbid",hide_input_in_errors=True)
    ticket: str=Field(min_length=30,max_length=100)


@router.post("/session")
def login(request:LoginRequest):
    try:
        return DashboardSessions().exchange(request.ticket)
    except PermissionError:
        raise HTTPException(401,"Sign-in link is invalid or expired.") from None


@router.get("/session")
def session(credentials:HTTPAuthorizationCredentials|None=Depends(bearer)):
    try:
        return DashboardSessions().verify(credentials.credentials if credentials else None)
    except PermissionError:
        raise HTTPException(401,"Owner session is invalid or expired.") from None


@router.delete("/session")
def logout(credentials:HTTPAuthorizationCredentials|None=Depends(bearer)):
    if credentials and credentials.credentials.startswith("sess_"):
        DashboardSessions().revoke(credentials.credentials)
    return {"revoked":True}
