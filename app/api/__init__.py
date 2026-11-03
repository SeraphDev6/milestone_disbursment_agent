"""HTTP API. `api_router` is always mounted; `mock_router` only in mock mode."""

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.api import (
    agreements,
    audit,
    auth,
    evidence,
    integrations,
    ledger,
    mock,
    orgs,
    paypal_accounts,
    settlement,
    sso,
)

api_router = APIRouter()


@api_router.get("/health", tags=["system"])
def health(request: Request) -> dict[str, str]:
    return {"status": "ok", "mode": request.app.state.settings.mode.value}


@api_router.get("/", include_in_schema=False)
def root(request: Request) -> RedirectResponse:
    """The front door: the web app when it's built, otherwise the API docs."""
    return RedirectResponse("/app/" if request.app.state.spa else "/docs", status_code=307)


for module in (auth, sso, orgs, paypal_accounts, agreements, settlement, ledger, evidence, integrations, audit):
    api_router.include_router(module.router)

mock_router = mock.router
