"""HTTP API. `api_router` is always mounted; `demo_router` when the demo is on (sandbox mode) and `mock_router`
when a stand-in integration is in use (tests)."""

from fastapi import APIRouter, Request

from app.api import (
    agreements,
    audit,
    auth,
    demo,
    email_evidence,
    evidence,
    github_app,
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
def health(request: Request) -> dict[str, str | bool]:
    """Liveness, which PayPal this deployment is connected to (sandbox or live), and whether /demo is on."""
    settings = request.app.state.settings
    return {"status": "ok", "mode": settings.mode.value, "demo": settings.demo_enabled}


for module in (
    auth,
    sso,
    orgs,
    paypal_accounts,
    github_app,
    agreements,
    settlement,
    ledger,
    evidence,
    integrations,
    email_evidence,
    audit,
):
    api_router.include_router(module.router)

mock_router = mock.router
demo_router = demo.router
