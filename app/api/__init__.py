"""HTTP API. `api_router` is always mounted; `mock_router` only in mock mode."""

from fastapi import APIRouter, Request

from app.api import agreements, audit, auth, evidence, integrations, mock, orgs, settlement, sso

api_router = APIRouter()


@api_router.get("/health", tags=["system"])
def health(request: Request) -> dict[str, str]:
    return {"status": "ok", "mode": request.app.state.settings.mode.value}


for module in (auth, sso, orgs, agreements, settlement, evidence, integrations, audit):
    api_router.include_router(module.router)

mock_router = mock.router
