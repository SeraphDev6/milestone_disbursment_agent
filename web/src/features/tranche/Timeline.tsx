import type { AuditEventRead } from "@/api/types";
import { actionText, actorText, type ActorNames } from "@/domain/actors";
import { dateTime } from "@/domain/format";
import { MILESTONE_STAMPS } from "@/domain/status";
import { Stamp } from "@/design/components";
import s from "./Tranche.module.css";

/** The tranche's record: who did what, when, newest last. */
export function Timeline({ events, names }: { events: AuditEventRead[]; names: ActorNames }) {
  if (!events.length) return <p style={{ color: "var(--text-secondary)" }}>Nothing has happened yet.</p>;
  return (
    <ol className={s.timeline} aria-label="Tranche history">
      {events.map((e, i) => {
        const to = e.to_status && e.to_status in MILESTONE_STAMPS ? MILESTONE_STAMPS[e.to_status as keyof typeof MILESTONE_STAMPS] : null;
        const why = (e.detail?.reason ?? e.detail?.note ?? e.detail?.error) as string | undefined;
        const doubted = e.detail?.skeptic_disagrees_on as string[] | undefined;
        const skepticFailed = e.detail?.skeptic_error as string | undefined;
        const handover = e.action === "handover_checked" ? (e.detail?.matches ? "Matches what was verified" : "Doesn't match what was verified") : null;
        return (
          <li key={i} className={s.tlItem}>
            <time className={s.tlWhen} dateTime={e.at}>{dateTime(e.at)}</time>
            <span className={s.tlWhat}>{actionText(e.action)} {to && <Stamp spec={to} flat explain={false} />}</span>
            <span className={s.tlWho}>{actorText(e.actor, names)}</span>
            {why && <span style={{ fontSize: "var(--text-sm)" }}>“{why}”</span>}
            {handover && <span style={{ fontSize: "var(--text-sm)" }}>{handover}</span>}
            {doubted?.length ? (
              <span style={{ fontSize: "var(--text-sm)" }}>Second opinion disagreed on {doubted.join(", ")}</span>
            ) : skepticFailed ? (
              <span style={{ fontSize: "var(--text-sm)" }}>Second opinion couldn't run, so a person decides</span>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
