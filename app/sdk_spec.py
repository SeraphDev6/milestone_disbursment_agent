"""Export the SDK's core API: the operations tagged `x-sdk-core`, with only the schemas they reference.

    python -m app.sdk_spec           write sdk/openapi-core.json
    python -m app.sdk_spec --check   exit 1 if the committed file is out of date (tests run this too)

The core spec is versioned on its own (CORE_VERSION, semver): a breaking change to a core operation bumps the
major version. Output is deterministic (sorted keys) so diffs show exactly what an SDK will see change.
"""

import json
import sys
from pathlib import Path

CORE_VERSION = "0.2.0"  # 0.2.0: operation ids, security, error responses, examples (spec quality pass, #14)
CORE_TAG = "x-sdk-core"  # its value is the SDK method name (operationId)
OUTPUT = Path(__file__).resolve().parent.parent / "sdk" / "openapi-core.json"
SERVERS = [{"url": "https://app.milestone-ledger.com/api", "description": "Hosted sandbox (PayPal sandbox, demo on)"}]

CONVENTIONS = """

## Conventions

- **Authentication:** `Authorization: Bearer <token>`, where the token is a person's session (from a magic link or
  SSO) or an organization API key (`POST /orgs/{id}/api-keys`). A person who belongs to several organizations sends
  `X-Org-Id` to say which one they act for.
- **Errors:** every 4xx body is `{"detail": "<what went wrong>"}` (422 carries FastAPI's field-level list in `detail`).
  401: no or expired token. 403: this org or role may not do that. 404: no such resource, or not yours to see.
  409: the resource isn't in a state that allows the call; the detail says what state it is in. 429: rate limited
  (`Retry-After` in seconds).
- **Idempotency and retries:** submitting identical evidence is a no-op (`duplicate: true`). Funding, releasing
  and paying out are safe to call again: an in-flight attempt is resumed with the same PayPal idempotency key, a
  finished one answers 409. Confirming with a stale `criteria_hash` answers 409 with the current criteria.
- **Pagination:** none. Lists (ledger, subscriptions, evidence) are per agreement or tranche and returned whole.
- **Money:** decimal strings with two places, in the agreement's ISO 4217 `currency`. Times are ISO 8601 with offset.
"""

ERROR_RESPONSES = {
    "401": "No, invalid or expired bearer token.",
    "403": "The caller's organization or role may not do this (e.g. the payee can't release; a payer can't approve "
    "withheld work).",
    "404": "No such agreement, tranche or subscription, or the caller's organization isn't a party to it.",
    "409": "The resource isn't in a state that allows this; `detail` says which state it is in.",
}
RATE_LIMITED = {"extractAgreement", "extractAgreementText", "precheckEvidence"}

# Request and response examples, keyed by schema name. Kept here, not on the models, so the API's own docs stay
# lean and every core request body has one (tests/test_sdk_spec.py checks).
EXAMPLES: dict[str, dict] = {
    "AgreementCreate": {
        "title": "Riverside Literacy Grant 2027",
        "total_amount": "30000.00",
        "currency": "USD",
        "settlement_mode": "escrow_release",
        "auto_release_threshold": 0.85,
        "counterparty_email": "director@riverside.example.org",
        "reviewer_emails": ["auditor@sponsor.example.org"],
        "milestones": [
            {
                "title": "Tranche 1: spring workshops",
                "amount": "10000.00",
                "due_date": "2027-05-31",
                "criteria": [
                    {
                        "statement": "At least 200 people attend the spring reading workshops",
                        "measure": {
                            "metric": "workshop attendees",
                            "comparator": "at_least",
                            "target": 200,
                            "unit": "people",
                        },
                        "evidence_required": ["spreadsheet"],
                        "evidence_description": "Signed sign-in sheets for each session, one row per attendee",
                        "verification": "check",
                        "check_id": "row_count",
                    }
                ],
            },
            {
                "title": "Tranche 2: outcomes report",
                "amount": "20000.00",
                "due_date": "2027-09-30",
                "criteria": [
                    {
                        "statement": "Final report shows pre and post reading scores for every enrolled student",
                        "measure": {"metric": "students with pre and post scores", "comparator": "exists"},
                        "evidence_required": ["document"],
                        "evidence_description": "Final outcomes report PDF with the anonymized score table",
                        "verification": "model",
                    }
                ],
            },
        ],
    },
    "AgreementText": {
        "text": "GRANT AGREEMENT between Harbor Foundation and Riverside Reads ... Tranche 1 (USD 10,000) is payable when ...",
        "title": "Riverside grant.txt",
    },
    "ConfirmRequest": {"criteria_hash": "3f1c…e9a2", "auto_release_enabled": False},
    "InviteToAgreement": {"email": "auditor@sponsor.example.org", "kind": "reviewer"},
    "EvidenceCreate": {
        "evidence_type": "spreadsheet",
        "title": "Spring workshop sign-in sheets (register).csv",
        "content": "session_date,session,attendee,signed\n2027-04-10,Session 1,Ava Nguyen,yes\n...",
        "dated": "2027-05-22",
    },
    "PrecheckRequest": {
        "evidence": [
            {
                "evidence_type": "document",
                "title": "Draft report.txt",
                "content": "Riverside Reads draft report, dated 2027-09-12 ...",
            }
        ]
    },
    "ReviewRequest": {"decision": "reject", "note": "Table 2 is missing the post-program scores for section B."},
    "SubscriptionCreate": {"url": "https://example.org/hooks/milestone-ledger", "events": ["milestone.status_changed"]},
    "EvidenceAccepted": {"evidence_id": "ev_01hx…", "duplicate": False, "milestone_status": "evidence_received"},
    "FundingStarted": {
        "order_id": "5O190127TN364715T",
        "approve_url": "https://www.sandbox.paypal.com/checkoutnow?token=5O190127TN364715T",
    },
    "ReviewOutcome": {
        "milestone_status": "funded",
        "missing": [
            {
                "criterion_id": "c1",
                "met": False,
                "decided_by": "model",
                "evidence_ids": [],
                "missing": "Post-program scores for section B (R-045 to R-087).",
            }
        ],
    },
}


def _refs(node, found: set[str]) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            found.add(ref.rsplit("/", 1)[-1])
        for v in node.values():
            _refs(v, found)
    elif isinstance(node, list):
        for v in node:
            _refs(v, found)


def _name_operations(paths: dict) -> None:
    """operationId = the x-sdk-core name (SDK generators turn it into the method name); drop the tag itself."""
    seen: set[str] = set()
    for path, ops in paths.items():
        for method, op in ops.items():
            name = op.pop(CORE_TAG)
            if not isinstance(name, str):
                raise TypeError(f"{method.upper()} {path}: x-sdk-core must name the operation")
            if name in seen:
                raise ValueError(f"operationId {name} is used twice")
            seen.add(name)
            op["operationId"] = name


def _add_responses(paths: dict) -> None:
    error = {"$ref": "#/components/schemas/ErrorResponse"}
    for ops in paths.values():
        for op in ops.values():
            responses = op.setdefault("responses", {})
            for code, text in ERROR_RESPONSES.items():
                responses.setdefault(code, {"description": text, "content": {"application/json": {"schema": error}}})
            if op["operationId"] in RATE_LIMITED:
                responses.setdefault(
                    "429",
                    {
                        "description": "Rate limited for this caller; `Retry-After` says when to try again.",
                        "content": {"application/json": {"schema": error}},
                    },
                )
            if "422" in responses:
                responses["422"]["description"] = "The request body or parameters failed validation."
            op["security"] = [{"bearerAuth": []}]


def _add_examples(schemas: dict) -> None:
    for name, example in EXAMPLES.items():
        if name in schemas:
            schemas[name]["examples"] = [example]


def core_spec(full: dict) -> dict:
    """Filter a full OpenAPI document down to the core operations and the component schemas they reach, name the
    operations, and add what generators and their users need: security, servers, uniform error responses, examples
    and the conventions."""
    paths = {}
    for path, item in full["paths"].items():
        ops = {m: op for m, op in item.items() if isinstance(op, dict) and op.get(CORE_TAG)}
        if ops:
            paths[path] = ops
    _name_operations(paths)
    _add_responses(paths)
    all_schemas = full.get("components", {}).get("schemas", {})
    keep: set[str] = set()
    frontier: set[str] = set()
    _refs(paths, frontier)
    while frontier:  # schemas reference other schemas
        name = frontier.pop()
        if name in keep or name not in all_schemas:
            continue
        keep.add(name)
        _refs(all_schemas[name], frontier)
    schemas = {n: all_schemas[n] for n in sorted(keep)}
    schemas["ErrorResponse"] = {
        "title": "ErrorResponse",
        "type": "object",
        "required": ["detail"],
        "properties": {
            "detail": {"type": "string", "title": "Detail", "description": "What went wrong, for a person."}
        },
    }
    _add_examples(schemas)
    components: dict = {
        "schemas": schemas,
        "securitySchemes": {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "description": "A person's session token or an organization API key. People in several "
                "organizations also send X-Org-Id.",
            }
        },
    }
    info = {
        **full["info"],
        "title": f"{full['info']['title']} (core)",
        "version": CORE_VERSION,
        "description": "The core flow an SDK covers: draft and confirm an agreement, fund a tranche, submit "
        "evidence, read the verdict, review and release. " + full["info"].get("description", "") + CONVENTIONS,
    }
    tags = [
        {"name": "agreements", "description": "Drafting, inviting, reading and signing agreements."},
        {"name": "evidence", "description": "Pre-checks, submissions, verdicts and review decisions on a tranche."},
        {"name": "settlement", "description": "Funding, releasing and paying out a tranche; the ledger."},
        {"name": "events", "description": "Signed outbound webhooks for an agreement's events."},
    ]
    used = {t for ops in paths.values() for op in ops.values() for t in op.get("tags", [])}
    return {
        "openapi": full["openapi"],
        "info": info,
        "servers": SERVERS,
        "tags": [t for t in tags if t["name"] in used],
        "security": [{"bearerAuth": []}],
        "paths": paths,
        "components": components,
    }


def render() -> str:
    from app.config import hermetic_settings
    from app.main import create_app

    # Ignore .env so the output doesn't depend on whoever runs it.
    full = create_app(hermetic_settings()).openapi()
    return json.dumps(core_spec(full), indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if argv == ["--check"]:
        if not OUTPUT.exists() or OUTPUT.read_text() != text:
            print(f"{OUTPUT.relative_to(Path.cwd())} is out of date; run `python -m app.sdk_spec`", file=sys.stderr)
            return 1
        print("core SDK spec is up to date")
        return 0
    if argv:
        print("usage: python -m app.sdk_spec [--check]", file=sys.stderr)
        return 2
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(text)
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
