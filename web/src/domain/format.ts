/** Money, dates and identifiers, formatted once. Amounts arrive from the API as decimal strings. */

const moneyFormats = new Map<string, Intl.NumberFormat>();

/** "20000.00", "USD" -> "20,000.00". The currency code is shown separately, ledger-style. */
export function amount(value: string | number | null | undefined, currency = "USD"): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return String(value);
  let f = moneyFormats.get(currency);
  if (!f) {
    f = new Intl.NumberFormat("en-US", { style: "decimal", minimumFractionDigits: 2, maximumFractionDigits: 2 });
    moneyFormats.set(currency, f);
  }
  return f.format(n);
}

/** For screen readers and sentences: "USD 20,000.00". */
export function money(value: string | number | null | undefined, currency = "USD"): string {
  return `${currency} ${amount(value, currency)}`;
}

export function toNumber(value: string | number | null | undefined): number {
  const n = typeof value === "number" ? value : Number(value ?? 0);
  return Number.isFinite(n) ? n : 0;
}

const dateFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });
const dateTimeFmt = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });

/** "2027-05-31" -> "31 May 2027". Date-only strings are calendar dates, never shifted by time zone. */
export function date(value: string | null | undefined): string {
  if (!value) return "—";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const [y, m, d] = value.split("-").map(Number) as [number, number, number];
    return dateFmt.format(new Date(Date.UTC(y, m - 1, d, 12)));
  }
  return dateFmt.format(new Date(value));
}

export function dateTime(value: string | null | undefined): string {
  return value ? dateTimeFmt.format(new Date(value)) : "—";
}

const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
export function relative(value: string | null | undefined, now = Date.now()): string {
  if (!value) return "—";
  const diff = (Date.parse(value) - now) / 1000;
  const abs = Math.abs(diff);
  if (abs < 60) return rtf.format(Math.round(diff), "second");
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  return rtf.format(Math.round(diff / 86400), "day");
}

/** "9f2c41…e41a" for display; the full value goes to the accessible name and the copy button. */
export function shortHash(hash: string, head = 4, tail = 4): string {
  return hash.length <= head + tail + 1 ? hash : `${hash.slice(0, head)}…${hash.slice(-tail)}`;
}

export function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

export const ROLE_LABEL = { payer: "Payer", payee: "Payee", reviewer: "Reviewer" } as const;
export const SIDE_NOUN = { payer: "Paying", payee: "Receiving", reviewer: "Reviewing" } as const;
export const MODE_LABEL = { escrow_release: "Escrow release", invoice_on_verify: "Invoice on verify" } as const;
export const COMPARATOR_LABEL = {
  at_least: "at least",
  at_most: "at most",
  equals: "equals",
  exists: "exists",
  on_or_before: "on or before",
} as const;
export const COMPARATOR_SYMBOL = { at_least: "≥", at_most: "≤", equals: "=", exists: "∃", on_or_before: "≤" } as const;
export const EVIDENCE_LABEL = {
  document: "Document",
  spreadsheet: "Spreadsheet",
  photo: "Photo",
  link: "Link",
  code_change: "Code change",
  ci_run: "CI run",
  dataset: "Dataset",
  attestation: "Attestation",
} as const;
export const VERIFICATION_LABEL = { check: "Deterministic check", model: "Verifier (cited)", human: "A person" } as const;

/** "at least 200 people", "on or before 30 Sep 2027", "exists". */
export function measureText(m: { comparator: keyof typeof COMPARATOR_LABEL; target?: string | number | null; unit?: string | null }): string {
  if (m.comparator === "exists") return "exists";
  const target = m.comparator === "on_or_before" && typeof m.target === "string" ? date(m.target) : String(m.target ?? "");
  return [COMPARATOR_LABEL[m.comparator], target, m.unit].filter(Boolean).join(" ");
}
