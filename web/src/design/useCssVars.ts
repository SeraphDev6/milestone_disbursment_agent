/** Read resolved CSS custom properties (for canvas charts, which can't use var()). Re-reads on theme change. */
import { useEffect, useState } from "react";
import { useTheme } from "./theme";

export function useCssVars<K extends string>(names: readonly K[]): Record<K, string> {
  const theme = useTheme();
  const read = () => {
    const cs = getComputedStyle(document.documentElement);
    return Object.fromEntries(names.map((n) => [n, cs.getPropertyValue(`--${n}`).trim()])) as Record<K, string>;
  };
  const [vals, setVals] = useState(read);
  const key = names.join(",");
  useEffect(() => {
    setVals(read());
    const queries = ["(prefers-color-scheme: dark)", "(prefers-contrast: more)"].map((q) => window.matchMedia(q));
    const onChange = () => setVals(read());
    queries.forEach((q) => q.addEventListener("change", onChange));
    return () => queries.forEach((q) => q.removeEventListener("change", onChange));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme, key]);
  return vals;
}
