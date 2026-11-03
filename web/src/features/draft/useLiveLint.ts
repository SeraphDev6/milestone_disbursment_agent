import { useEffect, useRef, useState } from "react";
import { lintDraft } from "@/api/queries";
import type { LintReport } from "@/api/types";
import { announce } from "@/design/components";
import { tranchesOut, type TrancheForm } from "./model";

/**
 * Runs the server's criteria lint as the draft changes (debounced, cancelling stale requests). The lint is
 * the same rule set that blocks signing, so what you see here is what the counterparty will see.
 * Announces only when the error count changes, so typing isn't drowned out.
 */
export function useLiveLint(total: string, tranches: TrancheForm[], enabled = true) {
  const [report, setReport] = useState<LintReport | null>(null);
  const [pending, setPending] = useState(false);
  const lastErrors = useRef<number | null>(null);
  const body = JSON.stringify({ total, ms: tranchesOut(tranches) });

  useEffect(() => {
    if (!enabled) return;
    const ctrl = new AbortController();
    setPending(true);
    const t = setTimeout(() => {
      const { total: tot, ms } = JSON.parse(body);
      lintDraft(Number(tot) > 0 ? tot : "0.01", ms, ctrl.signal)
        .then((r) => {
          setReport(r);
          const errors = r.issues?.filter((i) => i.level === "error").length ?? 0;
          if (lastErrors.current !== null && lastErrors.current !== errors)
            announce(errors ? `${errors} criteria problem${errors === 1 ? "" : "s"} to fix before signing` : "Criteria lint is clear");
          lastErrors.current = errors;
        })
        .catch(() => {
          /* malformed draft (e.g. empty fields): the client-side checks already explain it */
        })
        .finally(() => !ctrl.signal.aborted && setPending(false));
    }, 600);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [body, enabled]);

  return { report, pending };
}
