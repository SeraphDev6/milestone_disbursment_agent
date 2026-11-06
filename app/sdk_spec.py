"""Export the SDK's core API: the operations tagged `x-sdk-core`, with only the schemas they reference.

    python -m app.sdk_spec           write sdk/openapi-core.json
    python -m app.sdk_spec --check   exit 1 if the committed file is out of date (tests run this too)

The core spec is versioned on its own (CORE_VERSION, semver): a breaking change to a core operation bumps the
major version. Output is deterministic (sorted keys) so diffs show exactly what an SDK will see change.
"""

import json
import sys
from pathlib import Path

CORE_VERSION = "0.1.0"
CORE_TAG = "x-sdk-core"
OUTPUT = Path(__file__).resolve().parent.parent / "sdk" / "openapi-core.json"


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


def core_spec(full: dict) -> dict:
    """Filter a full OpenAPI document down to the core operations and the component schemas they reach."""
    paths = {}
    for path, item in full["paths"].items():
        ops = {m: op for m, op in item.items() if isinstance(op, dict) and op.get(CORE_TAG)}
        if ops:
            paths[path] = ops
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
    components: dict = {"schemas": {n: all_schemas[n] for n in sorted(keep)}}
    if "securitySchemes" in full.get("components", {}):
        components["securitySchemes"] = full["components"]["securitySchemes"]
    info = {
        **full["info"],
        "title": f"{full['info']['title']} (core)",
        "version": CORE_VERSION,
        "description": "The core flow an SDK covers: draft and confirm an agreement, fund a tranche, submit "
        "evidence, read the verdict, review and release. " + full["info"].get("description", ""),
    }
    return {"openapi": full["openapi"], "info": info, "paths": paths, "components": components}


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
