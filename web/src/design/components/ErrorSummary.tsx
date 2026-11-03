import { useEffect, useRef } from "react";
import { humanPath } from "@/domain/lint-paths";
import { Glyph } from "./Glyph";
import s from "./Surface.module.css";

export type Problem = { message: string; path?: string; href?: string };

/**
 * Shown at the top of a form after a failed submit. It takes focus so keyboard and screen-reader users
 * land on it, and each problem links to the field that has it (WCAG 3.3.1, 3.3.3).
 */
export function ErrorSummary({ title = "There's a problem", problems, focusKey }: { title?: string; problems: Problem[]; focusKey?: unknown }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (problems.length) ref.current?.focus();
  }, [focusKey, problems.length]);
  if (!problems.length) return null;
  return (
    <div ref={ref} tabIndex={-1} role="alert" aria-labelledby="error-summary-title" className={`${s.callout} ${s.error}`}>
      <Glyph name="warning" size={18} />
      <div className={s.calloutBody}>
        <p className={s.calloutTitle} id="error-summary-title">{title}</p>
        <ul style={{ margin: 0 }}>
          {problems.map((p, i) => (
            <li key={i}>
              {p.href ? (
                <a
                  href={p.href}
                  onClick={(e) => {
                    const el = document.getElementById(p.href!.slice(1));
                    if (el) {
                      e.preventDefault();
                      el.scrollIntoView({ block: "center" });
                      el.focus();
                    }
                  }}
                >
                  {p.path ? `${humanPath(p.path)}: ` : ""}
                  {p.message}
                </a>
              ) : (
                <>
                  {p.path ? `${humanPath(p.path)}: ` : ""}
                  {p.message}
                </>
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
