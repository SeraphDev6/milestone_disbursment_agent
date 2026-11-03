/** Mirror of the check registry in app/domain/checks.py, used to offer only checks that can run. */
import type { Comparator, EvidenceType } from "@/api/types";

export type CheckSpec = { id: string; label: string; description: string; evidence: EvidenceType[]; comparators: Comparator[] };

export const CHECKS: CheckSpec[] = [
  { id: "ci_green", label: "CI green", description: "All CI check runs pass on the pinned commit.", evidence: ["code_change", "ci_run"], comparators: ["exists"] },
  { id: "file_present", label: "File present", description: "A file matching the metric is attached to the evidence.", evidence: ["document", "spreadsheet", "dataset", "photo", "code_change"], comparators: ["exists"] },
  { id: "row_count", label: "Row count", description: "Number of data rows in an attached sheet compared to the target.", evidence: ["spreadsheet", "dataset"], comparators: ["at_least", "at_most", "equals"] },
  { id: "sheet_total", label: "Sheet total", description: "Sum of the metric's column in an attached sheet compared to the target.", evidence: ["spreadsheet"], comparators: ["at_least", "at_most", "equals"] },
  { id: "dated_by", label: "Dated by", description: "The evidence's own date is on or before the target.", evidence: ["document", "spreadsheet", "code_change", "photo"], comparators: ["on_or_before"] },
];

export function compatibleChecks(comparator: Comparator, evidence: EvidenceType[]): CheckSpec[] {
  return CHECKS.filter((c) => c.comparators.includes(comparator) && (evidence.length === 0 || evidence.some((e) => c.evidence.includes(e))));
}
