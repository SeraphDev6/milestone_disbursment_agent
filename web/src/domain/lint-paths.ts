/**
 * Lint and extraction paths ("milestones[0].criteria[1].statement") become stable DOM ids so an error
 * summary can link to the exact field and fields can describe themselves with their issues.
 */
import type { LintIssue } from "@/api/types";

export function fieldId(path: string): string {
  return "f-" + path.replace(/\[(\d+)\]/g, "-$1").replace(/\./g, "-");
}

export type PathParts = { milestone?: number; criterion?: number; field?: string };

export function parsePath(path: string): PathParts {
  const m = /milestones\[(\d+)\](?:\.criteria\[(\d+)\])?(?:\.(.+))?/.exec(path);
  if (!m) return { field: path };
  return { milestone: Number(m[1]), criterion: m[2] !== undefined ? Number(m[2]) : undefined, field: m[3] };
}

/** Issues that belong to one criterion (including its fields), errors first. */
export function issuesFor(issues: LintIssue[], milestone: number, criterion?: number): LintIssue[] {
  return issues
    .filter((i) => {
      const p = parsePath(i.path);
      return p.milestone === milestone && (criterion === undefined ? p.criterion === undefined : p.criterion === criterion);
    })
    .sort((a, b) => (a.level === b.level ? 0 : a.level === "error" ? -1 : 1));
}

export function humanPath(path: string): string {
  const p = parsePath(path);
  const parts: string[] = [];
  if (p.milestone !== undefined) parts.push(`Tranche ${p.milestone + 1}`);
  if (p.criterion !== undefined) parts.push(`criterion ${p.criterion + 1}`);
  if (p.field) parts.push(p.field.replace(/_/g, " ").replace(/\./g, " "));
  return parts.join(", ") || path;
}
