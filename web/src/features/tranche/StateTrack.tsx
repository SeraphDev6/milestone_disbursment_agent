import type { MilestoneStatus, SettlementMode } from "@/api/types";
import { ESCROW_TRACK, INVOICE_TRACK, MILESTONE_STAMPS, trackIndex } from "@/domain/status";
import { Glyph } from "@/design/components";
import s from "./Tranche.module.css";

/** Where the tranche is on its way to payment. Side states (review, dispute, failure) show at their step. */
export function StateTrack({ status, mode }: { status: MilestoneStatus; mode: SettlementMode }) {
  const track = mode === "escrow_release" ? ESCROW_TRACK : INVOICE_TRACK;
  const at = trackIndex(status, track);
  const side = !track.includes(status);
  return (
    <ol className={s.track} aria-label="Progress to payment">
      {track.map((st, i) => {
        const state = status === "settled" || i < at ? "done" : i === at ? "current" : "todo";
        const label = i === at && side ? MILESTONE_STAMPS[status].label : MILESTONE_STAMPS[st].label;
        return (
          <li key={st} className={`${s.trackStep} ${s[state] ?? ""}`} aria-current={state === "current" ? "step" : undefined}>
            <span className={s.stepMark}>
              {state === "done" && <Glyph name="check" size={12} />}
              {state === "current" && <Glyph name={MILESTONE_STAMPS[status].glyph} size={12} />}
              {label}
            </span>
            <span className="sr-only">{state === "done" ? "done" : state === "current" ? "current step" : "not yet"}</span>
          </li>
        );
      })}
    </ol>
  );
}
