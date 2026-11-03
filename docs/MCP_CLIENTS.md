# MCP client compatibility

What each MCP client expects from a remote server, and how this server meets it. Guarded by
`tests/test_mcp_clients.py`, which replays each client's documented behaviour (CIMD documents are the ones
the vendors publish). Researched Nov 3, 2026; re-check vendor docs before relying on this for a release.

**Connect at:** `https://<host>/mcp`, exactly. Claude requires the protected-resource `resource` to equal the
URL the user enters, path included.

## Requirements matrix

| Requirement | Who needs it | Status | Test |
| --- | --- | --- | --- |
| 401 with `WWW-Authenticate: Bearer resource_metadata="…", scope="mcp"` | All; Claude ignores it on non-401 | Yes | `test_metadata_meets_every_vendors_rules` |
| Protected-resource metadata at `/.well-known/oauth-protected-resource/mcp` **and** the root path | MCP spec (path first); ChatGPT documents the root | Both served | same |
| `resource` = the MCP URL, path included; one `authorization_servers` entry | Claude (uses only the first) | Yes | same |
| AS `issuer` equals `authorization_servers[0]` exactly | ChatGPT (exact string match) | Yes | same |
| `code_challenge_methods_supported` includes `S256` | MCP spec, Claude, ChatGPT (refuse without it) | Yes | same |
| `client_id_metadata_document_supported: true` and `"none"` in `token_endpoint_auth_methods_supported` | Claude (needs both to use CIMD), ChatGPT (prefers CIMD) | Yes | same |
| Client ID Metadata Documents: fetch https `client_id`, SSRF-safe, cached, validate `redirect_uris` | MCP spec SHOULD; Claude Code, ChatGPT | Yes (FastMCP `CIMDFetcher`) | `test_claude_code_cimd…`, `test_chatgpt_cimd…` |
| Loopback redirects match on any port for `localhost` and `127.0.0.1` | Claude Code (RFC 8252) | Yes | `test_claude_code_cimd_with_ephemeral_loopback_port` |
| ChatGPT CIMD prefers `private_key_jwt`, also supports `none` | ChatGPT | We advertise `none` (not `private_key_jwt`), so ChatGPT uses `none` | `test_chatgpt_cimd_public_client` |
| Dynamic Client Registration (`/register`, JSON body) | claude.ai fallback, ChatGPT fallback, VS Code, Cursor, Gemini CLI, Copilot CLI | Yes | `test_claude_ai_dcr`, `test_chatgpt_dcr…`, `test_ide_clients_dcr_loopback` |
| Accept `offline_access`; registered clients may request every advertised scope | Claude appends it when advertised; ChatGPT requests all advertised scopes | Yes (was a real bug: DCR clients without scope were refused) | `test_chatgpt_dcr_confidential_client` |
| Token endpoint takes `application/x-www-form-urlencoded` | Claude, everyone | Yes (SDK) | all flows |
| Rotate refresh tokens; `invalid_grant` when one is no longer valid | Claude, MCP spec (public clients MUST rotate) | Yes; reuse revokes the family | `test_claude_ai_dcr`, `test_refresh_rotates…` |
| `resource` on authorize and token requests; tokens only for this server | MCP spec, ChatGPT | Yes; tolerant of case and trailing slash only | `test_resource_must_be_this_server` |
| Consent page shows redirect host; warns on loopback-only redirects; shows CIMD host | MCP spec | Yes | `test_consent_names_cimd_client_and_warns_about_loopback` |
| CORS on `/mcp` and OAuth endpoints, exposing `WWW-Authenticate` and `Mcp-Session-Id` | Browser-based clients (MCP Inspector) | Yes | `test_cors_for_browser_based_clients` |
| Tool schemas without `$ref`/`$defs` | Gemini (API rejects), OpenAI strict mode, some Claude Code builds | Yes (FastMCP inlines) | `test_tool_definitions_are_portable` |
| Tool names `^[a-zA-Z0-9_-]{1,64}$` | OpenAI, Anthropic | Yes | same |
| server name + tool name at most 60 characters | Cursor (reported, unverified) | Yes (max 54) | same |
| Tool `annotations` (`title`, `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) | ChatGPT and Claude use them to decide when to ask before calling | Yes | same |
| Per-tool `_meta.securitySchemes` | ChatGPT (to trigger its auth UI) | `[{"type": "oauth2", "scopes": ["mcp"]}]` | same |
| Streamable HTTP | ChatGPT (required), everyone current | Yes (stateless, JSON responses) | `tests/test_mcp.py` |
| Endpoints answer within 10 s (30 s for refresh) | Claude | DB-only handlers; no outbound calls except CIMD fetch (10 s cap) | n/a |
| Static header credential (`Authorization: Bearer <org API key>`) | Claude `static_headers` (beta), headless agents | Yes | `test_org_api_key_works_for_headless_agents` |

## Can't be verified offline

- **Live connections** from claude.ai, ChatGPT, Claude Code, VS Code, Cursor and Gemini CLI. All need the
  server on public HTTPS (the Render deploy). Run the checklist below there.
- **ChatGPT mTLS / egress IPs**: optional hardening, not configured.
- **ChatGPT `private_key_jwt`**: not offered. ChatGPT falls back to `none` because we don't advertise it.
- **Cursor's 60-character limit** comes from community reports, not Cursor's docs.

## Live checklist (after deploy)

1. claude.ai → Settings → Connectors → Add custom connector → `https://<host>/mcp` → sign in → consent → ask
   "list my agreements".
2. Claude Code: `claude mcp add --transport http mde https://<host>/mcp`, then `/mcp` to authenticate.
3. ChatGPT (developer mode / plugins): add the MCP server URL, OAuth, sign in, run a read tool and
   `submit_evidence` (should prompt: not read-only).
4. VS Code (`.vscode/mcp.json` with `"type": "http"`), Cursor (`mcp.json` `url`), Gemini CLI
   (`gemini mcp add --transport http`).
5. MCP Inspector (`npx @modelcontextprotocol/inspector`) for browser CORS and the raw OAuth flow.

## Sources

- [OpenAI: Authentication for ChatGPT apps and plugins](https://developers.openai.com/plugins/build/auth)
- [ChatGPT CIMD document](https://chatgpt.com/oauth/client.json)
- [Anthropic: Authentication for connectors](https://claude.com/docs/connectors/building/authentication)
- [Claude Code CIMD document](https://claude.ai/oauth/claude-code-client-metadata)
- [MCP authorization spec 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
- [Gemini CLI MCP docs](https://google-gemini.github.io/gemini-cli/docs/tools/mcp-server.html)
- Gemini `$defs` issues: [#13326](https://github.com/google-gemini/gemini-cli/issues/13326), [#13142](https://github.com/google-gemini/gemini-cli/issues/13142); Claude Code `$ref` issue: [#76881](https://github.com/anthropics/claude-code/issues/76881)
- Copilot CLI DCR/CIMD: [#2717](https://github.com/github/copilot-cli/issues/2717), [#1305](https://github.com/github/copilot-cli/issues/1305)
