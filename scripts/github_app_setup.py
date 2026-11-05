"""Register this deployment's GitHub App in one click, with GitHub's app-manifest flow.

    uv run python scripts/github_app_setup.py --public-url https://app.milestone-ledger.com
    uv run python scripts/github_app_setup.py --public-url https://... --org my-github-org --name "Milestone Ledger"

It opens your browser on GitHub with the app's settings filled in (permissions, events, webhook and callback URLs).
After you click "Create GitHub App", GitHub sends the browser back to this script on 127.0.0.1, which exchanges the
one-time code for the app's credentials and writes them to `github-app.env` (mode 600, gitignored). Copy those
variables into the deployment's environment (Render: Environment > Add from .env) and restart.

The app asks for: contents, metadata and pull requests read; checks write. Events: pull_request (installation
events are always sent). User authorization during installation is on, which the install flow relies on.
"""

import argparse
import html
import http.server
import json
import os
import secrets
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

API_PREFIX = "/api"


def manifest(public_url: str, redirect_url: str, name: str, public: bool) -> dict:
    base = public_url.rstrip("/")
    return {
        "name": name,
        "url": base,
        "description": "Pays milestone tranches when merged, labelled pull requests meet the agreed criteria.",
        "hook_attributes": {"url": f"{base}{API_PREFIX}/webhooks/github/app", "active": True},
        "redirect_url": redirect_url,
        "callback_urls": [f"{base}{API_PREFIX}/github/app/callback"],
        "request_oauth_on_install": True,
        "setup_on_update": False,
        "public": public,
        "default_permissions": {"contents": "read", "metadata": "read", "pull_requests": "read", "checks": "write"},
        "default_events": ["pull_request"],
    }


def convert(code: str, transport: httpx.BaseTransport | None = None) -> dict:
    """Exchange the manifest flow's one-time code for the app's id, slug, client and webhook secrets and key."""
    with httpx.Client(timeout=20.0, transport=transport) as client:
        r = client.post(
            f"https://api.github.com/app-manifests/{code}/conversions",
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        )
    if r.status_code != 201:
        raise SystemExit(f"GitHub refused the code ({r.status_code}): {r.text[:300]}")
    return r.json()


def env_lines(app: dict) -> str:
    pem = app["pem"].strip().replace("\n", "\\n")  # one line, as most hosts' env editors expect
    return (
        "\n".join(
            [
                f"GITHUB_APP_ID={app['id']}",
                f"GITHUB_APP_SLUG={app['slug']}",
                f"GITHUB_APP_CLIENT_ID={app['client_id']}",
                f"GITHUB_APP_CLIENT_SECRET={app['client_secret']}",
                f"GITHUB_APP_WEBHOOK_SECRET={app['webhook_secret']}",
                f'GITHUB_APP_PRIVATE_KEY="{pem}"',
            ]
        )
        + "\n"
    )


def write_private(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--public-url", required=True, help="Where the deployment is served, e.g. https://app.example.org")
    p.add_argument("--name", default="Milestone Ledger", help="App name (unique on GitHub)")
    p.add_argument("--org", help="Create the app under this GitHub organization instead of your account")
    p.add_argument("--private", action="store_true", help="Only installable on the account that owns it")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--out", default="github-app.env")
    args = p.parse_args(argv)
    if not args.public_url.startswith("https://"):
        print("warning: GitHub needs a public https URL to deliver webhooks", file=sys.stderr)

    state = secrets.token_urlsafe(16)
    redirect = f"http://127.0.0.1:{args.port}/created"
    body = json.dumps(manifest(args.public_url, redirect, args.name, not args.private))
    owner = f"organizations/{args.org}/" if args.org else ""
    action = f"https://github.com/{owner}settings/apps/new?state={state}"
    result: dict = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _page(self, text: str, code: int = 200) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(text.encode())

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/":
                self._page(
                    "<!doctype html><title>Create the GitHub App</title><body onload='document.forms[0].submit()'>"
                    f"<form method=post action='{html.escape(action, quote=True)}'>"
                    f"<input type=hidden name=manifest value='{html.escape(body, quote=True)}'>"
                    "<button>Continue to GitHub</button></form>"
                )
                return
            if url.path == "/created":
                q = parse_qs(url.query)
                if q.get("state", [""])[0] != state or "code" not in q:
                    self._page("<p>That doesn't look like GitHub's answer to this setup run.</p>", 400)
                    return
                result.update(convert(q["code"][0]))
                self._page("<p>GitHub App created. You can close this tab and return to the terminal.</p>")
                done.set()
                return
            self._page("not found", 404)

    server = http.server.HTTPServer(("127.0.0.1", args.port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    start = f"http://127.0.0.1:{args.port}/"
    print(f"Opening {start} (it forwards to GitHub). If no browser opens, visit it yourself.")
    webbrowser.open(start)
    try:
        done.wait()
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()

    out = Path(args.out)
    write_private(out, env_lines(result))
    print(f"Created {result.get('html_url', result['slug'])} (app id {result['id']}).")
    print(f"Wrote its credentials to {out} (mode 600). Add them to the deployment's environment and restart.")
    print(
        f"Orgs install it from their organization settings in the app, or at https://github.com/apps/{result['slug']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
