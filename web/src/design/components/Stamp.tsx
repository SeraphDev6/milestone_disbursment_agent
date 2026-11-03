import { useEffect, useRef, useState } from "react";
import type { StampSpec } from "@/domain/status";
import { Glyph } from "./Glyph";
import s from "./Stamp.module.css";

type Props = {
  spec: StampSpec;
  size?: "md" | "lg";
  /** Lay the stamp flat, e.g. inside dense grid cells. */
  flat?: boolean;
  /** Prefix for the accessible name, e.g. "Tranche status". */
  context?: string;
  /** Show the meaning as a native tooltip as well as to assistive tech. */
  explain?: boolean;
};

/** A status, stamped. Animates (once) when the status changes, unless reduced motion is on. */
export function Stamp({ spec, size = "md", flat, context, explain = true }: Props) {
  const prev = useRef(spec.label);
  const [pressed, setPressed] = useState(false);
  useEffect(() => {
    if (prev.current !== spec.label) {
      prev.current = spec.label;
      setPressed(true);
      const t = setTimeout(() => setPressed(false), 400);
      return () => clearTimeout(t);
    }
  }, [spec.label]);
  const cls = [s.stamp, s[spec.tone], spec.final && s.final, flat && s.flat, size === "lg" && s.lg, pressed && s.press].filter(Boolean).join(" ");
  return (
    <span className={cls} title={explain ? spec.meaning : undefined}>
      <Glyph name={spec.glyph} size={size === "lg" ? 14 : 12} />
      <span>
        {context && <span className="sr-only">{context}: </span>}
        {spec.label}
        {explain && <span className="sr-only">. {spec.meaning}</span>}
      </span>
    </span>
  );
}
