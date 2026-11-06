"""The SDK's core spec (sdk/openapi-core.json) is committed, so SDK-facing API changes are deliberate."""

import json
import re

from app import sdk_spec

CORE = {
    ("post", "/agreements/extract"),
    ("post", "/agreements/extract-text"),
    ("post", "/orgs/{org_id}/agreements"),
    ("post", "/agreements/{agreement_id}/invitations"),
    ("get", "/agreements/{agreement_id}"),
    ("post", "/agreements/{agreement_id}/confirm"),
    ("post", "/milestones/{milestone_id}/fund"),
    ("post", "/milestones/{milestone_id}/precheck"),
    ("post", "/milestones/{milestone_id}/evidence"),
    ("post", "/milestones/{milestone_id}/uploads"),
    ("get", "/milestones/{milestone_id}/verdict"),
    ("post", "/milestones/{milestone_id}/review"),
    ("post", "/milestones/{milestone_id}/release"),
    ("post", "/milestones/{milestone_id}/pay-link"),
    ("post", "/milestones/{milestone_id}/payout"),
    ("get", "/agreements/{agreement_id}/ledger"),
    ("post", "/agreements/{agreement_id}/subscriptions"),
    ("get", "/agreements/{agreement_id}/subscriptions"),
    ("delete", "/agreements/{agreement_id}/subscriptions/{subscription_id}"),
}


def test_committed_core_spec_is_up_to_date():
    assert sdk_spec.OUTPUT.exists(), "run `python -m app.sdk_spec`"
    assert sdk_spec.OUTPUT.read_text() == sdk_spec.render(), (
        "sdk/openapi-core.json is stale: run `python -m app.sdk_spec`"
    )


def test_core_spec_has_exactly_the_core_operations_and_resolves_every_ref():
    spec = json.loads(sdk_spec.render())
    assert {(m, p) for p, ops in spec["paths"].items() for m in ops} == CORE
    assert spec["info"]["version"] == sdk_spec.CORE_VERSION
    refs: set[str] = set()
    sdk_spec._refs(spec, refs)
    assert refs <= set(spec["components"]["schemas"])  # self-contained
    assert "ExtractionResult" in spec["components"]["schemas"]
    assert not any("sso" in name.lower() for name in spec["components"]["schemas"])  # admin-only schemas left out


FASTAPI_OWN = {"HTTPValidationError", "ValidationError"}  # FastAPI's 422 body, not ours to describe


def test_core_spec_is_ready_for_sdk_generation():
    """What a generated SDK and its reader need: named operations, security, uniform errors, descriptions, examples."""
    spec = json.loads(sdk_spec.render())
    ops = [(m, p, op) for p, item in spec["paths"].items() for m, op in item.items()]
    ids = [op["operationId"] for _, _, op in ops]
    assert len(set(ids)) == len(ids) and all(re.fullmatch(r"[a-z][A-Za-z]+", i) for i in ids), ids
    assert "createAgreement" in ids and "submitEvidence" in ids and "getVerdict" in ids
    assert spec["security"] == [{"bearerAuth": []}] and "bearerAuth" in spec["components"]["securitySchemes"]
    assert spec["servers"][0]["url"].startswith("https://")
    for m, p, op in ops:
        assert op.get("summary"), f"{m} {p} has no summary"
        assert op["security"] == [{"bearerAuth": []}]
        for code in ("401", "403", "404", "409"):
            assert op["responses"][code]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorResponse"), (
                f"{m} {p} lacks a {code} response"
            )
        assert "x-sdk-core" not in op
    assert "Conventions" in spec["info"]["description"] and spec["tags"]

    schemas = spec["components"]["schemas"]
    undescribed = [
        f"{name}.{field}"
        for name, sch in schemas.items()
        if name not in FASTAPI_OWN and not name.startswith("Body_")
        for field, prop in (sch.get("properties") or {}).items()
        if "description" not in prop and "$ref" not in prop and not prop.get("allOf")
    ]
    assert not undescribed, f"fields without a description: {undescribed}"
    assert not [n for n, s in schemas.items() if "enum" in s and "description" not in s], "enum without a description"

    bodies = {
        ref.rsplit("/", 1)[-1]
        for _, _, op in ops
        for media in (op.get("requestBody") or {}).get("content", {}).values()
        if (ref := media.get("schema", {}).get("$ref"))
    }
    without_example = [b for b in bodies if not schemas[b].get("examples")]
    assert not without_example, f"request bodies without an example: {without_example}"
