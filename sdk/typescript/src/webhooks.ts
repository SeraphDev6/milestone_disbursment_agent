/**
 * Verify the engine's outbound webhooks and read their events.
 *
 * Signature scheme (app/services/outbound.py in the engine):
 *   X-MDE-Signature = "sha256=" + hex(HMAC-SHA256(secret, X-MDE-Timestamp + "." + raw body))
 *   X-MDE-Timestamp = Unix seconds when the delivery was signed
 *   X-MDE-Event     = the event type (also in the body)
 *   X-MDE-Delivery  = unique per delivery attempt
 *
 * Verify on the raw body exactly as received: re-serialising the JSON changes the signature. Uses Web Crypto, so
 * it runs on Node 20+, Deno, Bun, Cloudflare Workers and browsers.
 */

export const MILESTONE_STATUSES = [
  "pending",
  "funded",
  "evidence_received",
  "verifying",
  "needs_review",
  "verified",
  "settling",
  "held",
  "contested",
  "settled",
  "refunded",
  "settlement_failed",
  "payout_failed",
  "disputed",
  "overdue",
] as const;
export const AGREEMENT_STATUSES = ["draft", "awaiting_confirmation", "active", "completed", "cancelled"] as const;

export type MilestoneStatus = (typeof MILESTONE_STATUSES)[number];
export type AgreementStatus = (typeof AGREEMENT_STATUSES)[number];

export type EventData = {
  /** e.g. "evidence_received", "auto_verified", "captured", "payout_succeeded" */
  action: string;
  /** "user:<id>@org:<id>", "system:gate", "webhook:paypal", ... */
  actor: string;
  from_status: string | null;
  /** A MilestoneStatus for milestone.status_changed, an AgreementStatus for agreement.status_changed. */
  to_status: string | null;
  detail: Record<string, unknown>;
};

export type WebhookEvent = {
  /** Event id (also the audit log entry's id). */
  id: string;
  /** "milestone.status_changed", "milestone.<action>" or "agreement.<action>". */
  type: string;
  /** ISO 8601. */
  created_at: string;
  agreement_id: string;
  /** null for agreement-level events. */
  milestone_id: string | null;
  data: EventData;
  /** X-MDE-Delivery: unique per attempt; store it to drop retries you already handled. */
  delivery_id: string | null;
  /** "milestone" or "agreement". */
  scope: "milestone" | "agreement";
  is_status_change: boolean;
};

export type WebhookVerificationCode =
  | "missing_signature"
  | "missing_timestamp"
  | "bad_timestamp"
  | "timestamp_out_of_tolerance"
  | "signature_mismatch"
  | "bad_json"
  | "event_type_mismatch";

export class WebhookVerificationError extends Error {
  readonly code: WebhookVerificationCode;
  constructor(code: WebhookVerificationCode, message: string) {
    super(message);
    this.name = "WebhookVerificationError";
    this.code = code;
  }
}

export type VerifyOptions = {
  /** How old (or how far in the future) a delivery may be, in seconds. Retries carry a fresh timestamp. */
  toleranceSeconds?: number;
  /** The receiver's clock, in Unix seconds (tests). Defaults to Date.now(). */
  now?: number;
};

type HeaderValue = string | string[] | undefined | null;
type HeadersLike = Headers | Record<string, HeaderValue> | { get(name: string): string | null };

const SIGNATURE_HEADER = "X-MDE-Signature";
const TIMESTAMP_HEADER = "X-MDE-Timestamp";
const EVENT_HEADER = "X-MDE-Event";
const DELIVERY_HEADER = "X-MDE-Delivery";

function header(headers: HeadersLike, name: string): string | null {
  if (typeof (headers as { get?: unknown }).get === "function") {
    const v = (headers as { get(name: string): string | null }).get(name);
    return v == null ? null : String(v);
  }
  const record = headers as Record<string, HeaderValue>;
  const lowered = name.toLowerCase();
  for (const key of Object.keys(record)) {
    if (key.toLowerCase() === lowered) {
      const v = record[key];
      if (v == null) return null;
      return Array.isArray(v) ? String(v[0]) : String(v);
    }
  }
  return null;
}

const encoder = new TextEncoder();

function toBytes(body: string | Uint8Array | ArrayBuffer): Uint8Array {
  if (typeof body === "string") return encoder.encode(body);
  return body instanceof Uint8Array ? body : new Uint8Array(body);
}

function hex(bytes: ArrayBuffer): string {
  return Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** The signature the engine would put on this delivery. */
export async function expectedSignature(secret: string, timestamp: string, rawBody: string | Uint8Array | ArrayBuffer): Promise<string> {
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const body = toBytes(rawBody);
  const prefix = encoder.encode(timestamp + ".");
  const message = new Uint8Array(prefix.length + body.length);
  message.set(prefix, 0);
  message.set(body, prefix.length);
  return "sha256=" + hex(await crypto.subtle.sign("HMAC", key, message));
}

/** Constant-time string comparison (the Web Crypto API has no timingSafeEqual). */
function equalConstantTime(a: string, b: string): boolean {
  const x = encoder.encode(a);
  const y = encoder.encode(b);
  let diff = x.length ^ y.length;
  const n = Math.max(x.length, y.length);
  for (let i = 0; i < n; i++) diff |= (x[i % x.length] ?? 0) ^ (y[i % y.length] ?? 0);
  return diff === 0;
}

/**
 * Check the signature and timestamp of one delivery and return its event.
 *
 * @param rawBody the request body exactly as received (string, bytes or ArrayBuffer)
 * @param headers the request headers (any case): a Headers object or a plain record (Node's req.headers)
 * @param secret the subscription's secret, shown once when it was created
 */
export async function verifyWebhook(
  rawBody: string | Uint8Array | ArrayBuffer,
  headers: HeadersLike,
  secret: string,
  options: VerifyOptions = {},
): Promise<WebhookEvent> {
  const tolerance = options.toleranceSeconds ?? 300;
  const signature = header(headers, SIGNATURE_HEADER);
  const timestamp = header(headers, TIMESTAMP_HEADER);
  if (!signature) throw new WebhookVerificationError("missing_signature", `no ${SIGNATURE_HEADER} header`);
  if (!timestamp) throw new WebhookVerificationError("missing_timestamp", `no ${TIMESTAMP_HEADER} header`);
  if (!/^\d+$/.test(timestamp)) throw new WebhookVerificationError("bad_timestamp", `${TIMESTAMP_HEADER} is not Unix seconds: ${timestamp}`);
  const now = options.now ?? Date.now() / 1000;
  const age = now - Number(timestamp);
  if (Math.abs(age) > tolerance) {
    throw new WebhookVerificationError("timestamp_out_of_tolerance", `delivery signed ${Math.trunc(age)}s ago; more than ${tolerance}s (a replay, or clocks are off)`);
  }
  const body = toBytes(rawBody);
  if (!equalConstantTime(signature, await expectedSignature(secret, timestamp, body))) {
    throw new WebhookVerificationError("signature_mismatch", "signature doesn't match the body and secret");
  }
  let payload: unknown;
  try {
    payload = JSON.parse(new TextDecoder().decode(body));
  } catch (e) {
    throw new WebhookVerificationError("bad_json", `body is not JSON: ${(e as Error).message}`);
  }
  if (!payload || typeof payload !== "object" || typeof (payload as { type?: unknown }).type !== "string") {
    throw new WebhookVerificationError("bad_json", "body is not an event object");
  }
  const p = payload as Record<string, unknown>;
  const announced = header(headers, EVENT_HEADER);
  if (announced && announced !== p.type) {
    throw new WebhookVerificationError("event_type_mismatch", `${EVENT_HEADER} says ${announced} but the body is ${p.type}`);
  }
  const data = (p.data ?? {}) as Record<string, unknown>;
  const type = p.type as string;
  const toStatus = (data.to_status as string | null | undefined) ?? null;
  return {
    id: String(p.id ?? ""),
    type,
    created_at: String(p.created_at ?? ""),
    agreement_id: String(p.agreement_id ?? ""),
    milestone_id: (p.milestone_id as string | null | undefined) ?? null,
    data: {
      action: String(data.action ?? ""),
      actor: String(data.actor ?? ""),
      from_status: (data.from_status as string | null | undefined) ?? null,
      to_status: toStatus,
      detail: ((data.detail as Record<string, unknown> | null | undefined) ?? {}) as Record<string, unknown>,
    },
    delivery_id: header(headers, DELIVERY_HEADER),
    scope: type.startsWith("agreement.") ? "agreement" : "milestone",
    is_status_change: type.endsWith(".status_changed"),
  };
}
