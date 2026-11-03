/** Theme choice: follow the OS (default) or pin Paper, Carbon or Contrast. Applied as <html data-theme>. */
import { useSyncExternalStore } from "react";
import type { Theme } from "./palette";

export type ThemeChoice = Theme | "system";
const KEY = "mde.theme";
const subs = new Set<() => void>();

function read(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    return v === "paper" || v === "carbon" || v === "contrast" ? v : "system";
  } catch {
    return "system";
  }
}

let current: ThemeChoice = typeof window === "undefined" ? "system" : read();

export function setTheme(t: ThemeChoice) {
  current = t;
  try {
    if (t === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, t);
  } catch {
    /* storage unavailable: the choice lasts for this page view */
  }
  if (t === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
  subs.forEach((f) => f());
}

export function useTheme(): ThemeChoice {
  return useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => subs.delete(f);
    },
    () => current,
    () => "system",
  );
}

export const THEME_OPTIONS: { id: ThemeChoice; label: string; description: string }[] = [
  { id: "system", label: "Match my device", description: "Paper or Carbon, and Contrast when you've asked your device for more contrast." },
  { id: "paper", label: "Paper", description: "Light: warm paper, dark ink." },
  { id: "carbon", label: "Carbon", description: "Dark: charcoal, light ink." },
  { id: "contrast", label: "Contrast", description: "Maximum contrast: black on white, 7:1 text." },
];
