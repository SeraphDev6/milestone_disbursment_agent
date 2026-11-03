import { Glyph } from "@/design/components";
import s from "./Steps.module.css";

/** Wizard progress. A list with aria-current="step"; completed steps can be revisited. */
export function Steps({ steps, current, onGo, label }: { steps: string[]; current: number; onGo: (i: number) => void; label: string }) {
  return (
    <nav aria-label={label}>
      <ol className={s.steps}>
        {steps.map((name, i) => {
          const state = i < current ? "done" : i === current ? "current" : "todo";
          return (
            <li key={name} className={`${s.step} ${s[state]}`} aria-current={state === "current" ? "step" : undefined}>
              {state === "done" ? (
                <button type="button" className={s.stepButton} onClick={() => onGo(i)}>
                  <span className={s.num}><Glyph name="check" size={12} /></span>
                  {name}
                  <span className="sr-only"> (completed, go back)</span>
                </button>
              ) : (
                <span className={s.stepButton}>
                  <span className={s.num}>{i + 1}</span>
                  {name}
                  {state === "todo" && <span className="sr-only"> (not started)</span>}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
