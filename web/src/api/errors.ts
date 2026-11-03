/**
 * Every failed call becomes an ApiError. FastAPI sends `detail` as a string, as a structured object
 * ({message, lint, current_criteria_hash}) or, for request validation, as a list of {loc, msg}.
 */
import type { LintReport } from "./types";

export type FieldProblem = { path: string; message: string };

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  readonly lint?: LintReport;
  readonly currentCriteriaHash?: string;
  readonly fields: FieldProblem[];

  constructor(status: number, detail: unknown) {
    super(messageFrom(status, detail));
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.fields = [];
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      const d = detail as Record<string, unknown>;
      if (d.lint) this.lint = d.lint as LintReport;
      if (typeof d.current_criteria_hash === "string") this.currentCriteriaHash = d.current_criteria_hash;
    }
    if (Array.isArray(detail)) {
      this.fields = detail.map((e: { loc?: (string | number)[]; msg?: string }) => ({
        path: locToPath(e.loc ?? []),
        message: cleanMsg(e.msg ?? "is invalid"),
      }));
    }
  }

  get isNetwork() {
    return this.status === 0;
  }
}

/** ["body", "milestones", 0, "criteria", 1, "statement"] -> "milestones[0].criteria[1].statement" */
export function locToPath(loc: (string | number)[]): string {
  const parts = loc[0] === "body" || loc[0] === "query" || loc[0] === "path" ? loc.slice(1) : loc;
  return parts.reduce<string>((acc, p) => (typeof p === "number" ? `${acc}[${p}]` : acc ? `${acc}.${p}` : String(p)), "");
}

function cleanMsg(msg: string): string {
  return msg.replace(/^Value error, /, "");
}

function messageFrom(status: number, detail: unknown): string {
  if (typeof detail === "string") return capitalize(detail);
  if (detail && typeof detail === "object" && !Array.isArray(detail) && typeof (detail as { message?: unknown }).message === "string")
    return capitalize((detail as { message: string }).message);
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] as { loc?: (string | number)[]; msg?: string };
    const where = first.loc ? locToPath(first.loc) : "";
    return `${where ? where + ": " : ""}${cleanMsg(first.msg ?? "is invalid")}${detail.length > 1 ? ` (and ${detail.length - 1} more)` : ""}`;
  }
  if (status === 0) return "Can't reach the server. Check your connection and try again.";
  if (status >= 500) return "The server ran into a problem. Try again in a moment.";
  return `Request failed (${status}).`;
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "Something went wrong.";
}
