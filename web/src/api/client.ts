/**
 * The typed API client. Paths and bodies are checked against the generated OpenAPI schema.
 * Use `call(api.GET(...))` to get data or throw an ApiError.
 */
import createClient, { type Middleware } from "openapi-fetch";
import { sessions } from "@/auth/session";
import { ApiError } from "./errors";
import type { paths } from "./schema";

const auth: Middleware = {
  onRequest({ request }) {
    const token = sessions.token();
    if (token && !request.headers.has("Authorization")) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
  onResponse({ request, response }) {
    // The token is expired or revoked: drop that session so the app routes to sign-in.
    if (response.status === 401 && request.headers.get("Authorization")) {
      const active = sessions.active();
      if (active && request.headers.get("Authorization") === `Bearer ${active.token}`) sessions.remove(active.user.id);
    }
    return response;
  },
};

/** The REST API lives under this prefix on the same host (the MCP server is at /mcp, the app at /). */
export const API_BASE = "/api";

export const api = createClient<paths>({ baseUrl: API_BASE });
api.use(auth);

type Result<T> = { data?: T; error?: unknown; response: Response };

/** Unwrap an openapi-fetch result: return the data or throw an ApiError with the server's detail. */
export async function call<T>(p: Promise<Result<T>>): Promise<T> {
  let r: Result<T>;
  try {
    r = await p;
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, null);
  }
  if (!r.response.ok) {
    const err = r.error as { detail?: unknown } | undefined;
    throw new ApiError(r.response.status, err && typeof err === "object" && "detail" in err ? err.detail : err);
  }
  return r.data as T;
}

/** Header that says which org you act for on an agreement (needed when you sit on several of its parties). */
export function asOrg(orgId: string | undefined): { headers: Record<string, string> } {
  return { headers: orgId ? { "X-Org-Id": orgId } : {} };
}

/** Authenticated download (files and PDFs aren't JSON): fetch with the bearer token, then save the blob. */
/** Downloads a file. Returns the server's X-Content-SHA256 (when sent) and whether the bytes received hash to it. */
export async function download(path: string, fallbackName: string, orgId?: string): Promise<{ sha256: string | null; matches: boolean | null }> {
  const token = sessions.token();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (orgId) headers["X-Org-Id"] = orgId;
  let res: Response;
  try {
    res = await fetch(API_BASE + path, { headers });
  } catch {
    throw new ApiError(0, null);
  }
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? fallbackName;
  const blob = await res.blob();
  const sha256 = res.headers.get("X-Content-SHA256");
  let matches: boolean | null = null;
  if (sha256 && globalThis.crypto?.subtle) {
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", await blob.arrayBuffer()));
    matches = Array.from(digest, (b) => b.toString(16).padStart(2, "0")).join("") === sha256;
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
  return { sha256, matches };
}
