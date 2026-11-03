/**
 * One polite and one assertive live region for the whole app (WCAG 4.1.3). Components call announce()
 * instead of mounting their own regions, which screen readers often miss when created late.
 */
import { useEffect, useState } from "react";

type Politeness = "polite" | "assertive";
type Listener = (msg: string, p: Politeness) => void;
const listeners = new Set<Listener>();

export function announce(message: string, politeness: Politeness = "polite") {
  for (const l of listeners) l(message, politeness);
}

export function Announcer() {
  const [polite, setPolite] = useState("");
  const [assertive, setAssertive] = useState("");
  useEffect(() => {
    const l: Listener = (msg, p) => {
      const setter = p === "assertive" ? setAssertive : setPolite;
      // Clear first so repeating the same message is announced again.
      setter("");
      requestAnimationFrame(() => setter(msg));
    };
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);
  return (
    <>
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">{polite}</div>
      <div className="sr-only" role="alert" aria-live="assertive" aria-atomic="true">{assertive}</div>
    </>
  );
}
