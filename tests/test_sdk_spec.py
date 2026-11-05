"""The SDK's core spec (sdk/openapi-core.json) is committed, so SDK-facing API changes are deliberate."""

import json

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
