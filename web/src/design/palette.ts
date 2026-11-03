/**
 * The single source of truth for colour. `npm run tokens` writes themes.css from this file and fails if
 * any declared pair in PAIRS misses its WCAG contrast minimum in any theme. Edit colours here, never in CSS.
 *
 * Colours are OKLCH (perceptual lightness makes ramps predictable). Names are semantic: say what a colour is
 * for, not what it looks like.
 */

export const THEMES = ["paper", "carbon", "contrast"] as const;
export type Theme = (typeof THEMES)[number];

export const TONES = ["settled", "held", "review", "alert", "neutral"] as const;
export type Tone = (typeof TONES)[number];

type Swatch = string; // any CSS colour culori can parse

export type ThemeColors = {
  "surface-page": Swatch;
  "surface-sheet": Swatch;
  "surface-sunken": Swatch;
  "surface-raised": Swatch;
  "surface-inverse": Swatch;
  "text-primary": Swatch;
  "text-secondary": Swatch;
  "text-inverse": Swatch;
  "text-link": Swatch;
  "text-success": Swatch; // "done", "passes", signed: on plain surfaces (stamp inks are only for stamp surfaces)
  "rule-hairline": Swatch; // decorative only: never the sole boundary of a control
  "rule-strong": Swatch;
  "border-control": Swatch;
  "focus-ring": Swatch;
  "focus-halo": Swatch; // inner contrast band so the ring reads on any surface
  accent: Swatch;
  "accent-hover": Swatch;
  "on-accent": Swatch;
  danger: Swatch;
  "danger-hover": Swatch;
  "on-danger": Swatch;
  highlight: Swatch; // <mark>, selected rows
  "on-highlight": Swatch;
  // Chart series (portfolio): validated with the dataviz palette checker for band, chroma and CVD separation.
  "chart-released": Swatch;
  "chart-held": Swatch;
  "chart-window": Swatch;
  "chart-invoiced": Swatch;
} & { [K in `stamp-${Tone}-ink` | `stamp-${Tone}-surface` | `stamp-${Tone}-edge` | `callout-${Tone}-surface` | `callout-${Tone}-edge`]: Swatch };

export const PALETTE: Record<Theme, ThemeColors> = {
  // Paper: a ledger page. Warm off-white, brown-black ink, registrar-blue accent.
  paper: {
    "surface-page": "oklch(0.958 0.014 84)",
    "surface-sheet": "oklch(0.988 0.007 86)",
    "surface-sunken": "oklch(0.928 0.018 82)",
    "surface-raised": "oklch(0.995 0.004 86)",
    "surface-inverse": "oklch(0.26 0.02 60)",
    "text-primary": "oklch(0.25 0.022 58)",
    "text-secondary": "oklch(0.44 0.026 62)",
    "text-inverse": "oklch(0.96 0.012 84)",
    "text-link": "oklch(0.42 0.12 258)",
    "text-success": "oklch(0.40 0.10 152)",
    "rule-hairline": "oklch(0.84 0.022 78)",
    "rule-strong": "oklch(0.36 0.022 60)",
    "border-control": "oklch(0.56 0.028 66)",
    "focus-ring": "oklch(0.44 0.17 262)",
    "focus-halo": "oklch(0.995 0.004 86)",
    accent: "oklch(0.38 0.11 258)",
    "accent-hover": "oklch(0.31 0.10 258)",
    "on-accent": "oklch(0.985 0.006 86)",
    danger: "oklch(0.46 0.16 29)",
    "danger-hover": "oklch(0.39 0.14 29)",
    "on-danger": "oklch(0.985 0.006 86)",
    highlight: "oklch(0.90 0.07 92)",
    "on-highlight": "oklch(0.25 0.022 58)",
    "chart-released": "oklch(0.50 0.12 152)",
    "chart-held": "oklch(0.46 0.15 266)",
    "chart-window": "oklch(0.62 0.11 220)",
    "chart-invoiced": "oklch(0.66 0.13 70)",
    "stamp-settled-ink": "oklch(0.40 0.10 152)",
    "stamp-settled-surface": "oklch(0.945 0.035 150)",
    "stamp-settled-edge": "oklch(0.40 0.10 152)",
    "callout-settled-surface": "oklch(0.945 0.035 150)",
    "callout-settled-edge": "oklch(0.40 0.10 152)",
    "stamp-held-ink": "oklch(0.40 0.11 256)",
    "stamp-held-surface": "oklch(0.945 0.025 250)",
    "stamp-held-edge": "oklch(0.40 0.11 256)",
    "callout-held-surface": "oklch(0.945 0.025 250)",
    "callout-held-edge": "oklch(0.40 0.11 256)",
    "stamp-review-ink": "oklch(0.45 0.10 68)",
    "stamp-review-surface": "oklch(0.94 0.05 85)",
    "stamp-review-edge": "oklch(0.45 0.10 68)",
    "callout-review-surface": "oklch(0.94 0.05 85)",
    "callout-review-edge": "oklch(0.45 0.10 68)",
    "stamp-alert-ink": "oklch(0.45 0.16 29)",
    "stamp-alert-surface": "oklch(0.945 0.03 30)",
    "stamp-alert-edge": "oklch(0.45 0.16 29)",
    "callout-alert-surface": "oklch(0.945 0.03 30)",
    "callout-alert-edge": "oklch(0.45 0.16 29)",
    "stamp-neutral-ink": "oklch(0.42 0.012 70)",
    "stamp-neutral-surface": "oklch(0.935 0.01 80)",
    "stamp-neutral-edge": "oklch(0.42 0.012 70)",
    "callout-neutral-surface": "oklch(0.935 0.01 80)",
    "callout-neutral-edge": "oklch(0.42 0.012 70)",
  },
  // Carbon: a carbon copy. Charcoal paper, bone ink, inks lifted to stay readable.
  carbon: {
    "surface-page": "oklch(0.195 0.009 70)",
    "surface-sheet": "oklch(0.235 0.011 70)",
    "surface-sunken": "oklch(0.165 0.008 70)",
    "surface-raised": "oklch(0.27 0.012 70)",
    "surface-inverse": "oklch(0.92 0.018 84)",
    "text-primary": "oklch(0.93 0.018 84)",
    "text-secondary": "oklch(0.76 0.02 80)",
    "text-inverse": "oklch(0.22 0.012 70)",
    "text-link": "oklch(0.80 0.09 252)",
    "text-success": "oklch(0.80 0.10 152)",
    "rule-hairline": "oklch(0.34 0.012 75)",
    "rule-strong": "oklch(0.80 0.02 82)",
    "border-control": "oklch(0.60 0.02 80)",
    "focus-ring": "oklch(0.82 0.11 250)",
    "focus-halo": "oklch(0.165 0.008 70)",
    accent: "oklch(0.80 0.085 252)",
    "accent-hover": "oklch(0.86 0.07 252)",
    "on-accent": "oklch(0.20 0.02 255)",
    danger: "oklch(0.74 0.13 30)",
    "danger-hover": "oklch(0.80 0.11 30)",
    "on-danger": "oklch(0.19 0.02 30)",
    highlight: "oklch(0.40 0.06 90)",
    "on-highlight": "oklch(0.95 0.015 84)",
    "chart-released": "oklch(0.55 0.12 152)",
    "chart-held": "oklch(0.55 0.15 268)",
    "chart-window": "oklch(0.665 0.11 215)",
    "chart-invoiced": "oklch(0.66 0.12 70)",
    "stamp-settled-ink": "oklch(0.80 0.10 152)",
    "stamp-settled-surface": "oklch(0.26 0.03 152)",
    "stamp-settled-edge": "oklch(0.80 0.10 152)",
    "callout-settled-surface": "oklch(0.26 0.03 152)",
    "callout-settled-edge": "oklch(0.80 0.10 152)",
    "stamp-held-ink": "oklch(0.80 0.08 252)",
    "stamp-held-surface": "oklch(0.26 0.03 252)",
    "stamp-held-edge": "oklch(0.80 0.08 252)",
    "callout-held-surface": "oklch(0.26 0.03 252)",
    "callout-held-edge": "oklch(0.80 0.08 252)",
    "stamp-review-ink": "oklch(0.83 0.10 80)",
    "stamp-review-surface": "oklch(0.27 0.03 75)",
    "stamp-review-edge": "oklch(0.83 0.10 80)",
    "callout-review-surface": "oklch(0.27 0.03 75)",
    "callout-review-edge": "oklch(0.83 0.10 80)",
    "stamp-alert-ink": "oklch(0.77 0.12 30)",
    "stamp-alert-surface": "oklch(0.27 0.035 30)",
    "stamp-alert-edge": "oklch(0.77 0.12 30)",
    "callout-alert-surface": "oklch(0.27 0.035 30)",
    "callout-alert-edge": "oklch(0.77 0.12 30)",
    "stamp-neutral-ink": "oklch(0.78 0.012 80)",
    "stamp-neutral-surface": "oklch(0.27 0.008 75)",
    "stamp-neutral-edge": "oklch(0.78 0.012 80)",
    "callout-neutral-surface": "oklch(0.27 0.008 75)",
    "callout-neutral-edge": "oklch(0.78 0.012 80)",
  },
  // Contrast: for low vision and bright rooms. White sheet, black ink, every pair at 7:1 or more.
  contrast: {
    "surface-page": "oklch(1 0 0)",
    "surface-sheet": "oklch(1 0 0)",
    "surface-sunken": "oklch(0.96 0 0)",
    "surface-raised": "oklch(1 0 0)",
    "surface-inverse": "oklch(0 0 0)",
    "text-primary": "oklch(0 0 0)",
    "text-secondary": "oklch(0.28 0 0)",
    "text-inverse": "oklch(1 0 0)",
    "text-link": "oklch(0.30 0.16 264)",
    "text-success": "oklch(0.32 0.10 152)",
    "rule-hairline": "oklch(0.30 0 0)",
    "rule-strong": "oklch(0 0 0)",
    "border-control": "oklch(0 0 0)",
    "focus-ring": "oklch(0 0 0)",
    "focus-halo": "oklch(0.90 0.18 100)",
    accent: "oklch(0.27 0.14 264)",
    "accent-hover": "oklch(0 0 0)",
    "on-accent": "oklch(1 0 0)",
    danger: "oklch(0.36 0.15 29)",
    "danger-hover": "oklch(0 0 0)",
    "on-danger": "oklch(1 0 0)",
    highlight: "oklch(0.92 0.17 100)",
    "on-highlight": "oklch(0 0 0)",
    "chart-released": "oklch(0.40 0.10 152)",
    "chart-held": "oklch(0.38 0.13 258)",
    "chart-window": "oklch(0.38 0.09 220)",
    "chart-invoiced": "oklch(0.45 0.10 68)",
    "stamp-settled-ink": "oklch(1 0 0)",
    "stamp-settled-surface": "oklch(0.32 0.10 152)",
    "stamp-settled-edge": "oklch(0.32 0.10 152)",
    "callout-settled-surface": "oklch(1 0 0)",
    "callout-settled-edge": "oklch(0.32 0.10 152)",
    "stamp-held-ink": "oklch(1 0 0)",
    "stamp-held-surface": "oklch(0.30 0.13 262)",
    "stamp-held-edge": "oklch(0.30 0.13 262)",
    "callout-held-surface": "oklch(1 0 0)",
    "callout-held-edge": "oklch(0.30 0.13 262)",
    "stamp-review-ink": "oklch(0 0 0)",
    "stamp-review-surface": "oklch(0.88 0.15 92)",
    "stamp-review-edge": "oklch(0 0 0)", // yellow fill needs a black edge,
    "callout-review-surface": "oklch(1 0 0)",
    "callout-review-edge": "oklch(0 0 0)",
    "stamp-alert-ink": "oklch(1 0 0)",
    "stamp-alert-surface": "oklch(0.38 0.15 29)",
    "stamp-alert-edge": "oklch(0.38 0.15 29)",
    "callout-alert-surface": "oklch(1 0 0)",
    "callout-alert-edge": "oklch(0.38 0.15 29)",
    "stamp-neutral-ink": "oklch(1 0 0)",
    "stamp-neutral-surface": "oklch(0.25 0 0)",
    "stamp-neutral-edge": "oklch(0.25 0 0)",
    "callout-neutral-surface": "oklch(1 0 0)",
    "callout-neutral-edge": "oklch(0.25 0 0)",
  },
};

/** What each pair is for decides its minimum: text needs 4.5:1, UI boundaries 3:1 (WCAG 1.4.3, 1.4.11). */
export type PairKind = "text" | "ui";
export type Pair = { fg: keyof ThemeColors; bg: keyof ThemeColors; kind: PairKind; why: string };

const surfaces = ["surface-page", "surface-sheet", "surface-sunken", "surface-raised"] as const;

export const PAIRS: Pair[] = [
  ...surfaces.flatMap((bg): Pair[] => [
    { fg: "text-primary", bg, kind: "text", why: "body text" },
    { fg: "text-secondary", bg, kind: "text", why: "secondary text, captions" },
    { fg: "text-link", bg, kind: "text", why: "links" },
    { fg: "text-success", bg, kind: "text", why: "done, passed, signed" },
    { fg: "danger", bg, kind: "text", why: "error messages" },
    { fg: "border-control", bg, kind: "ui", why: "input and button outlines" },
    { fg: "focus-ring", bg, kind: "ui", why: "focus indicator" },
    { fg: "accent", bg, kind: "ui", why: "primary button fill against the page" },
    { fg: "rule-strong", bg, kind: "ui", why: "total rules, table header rules" },
    ...TONES.map((t): Pair => ({ fg: `stamp-${t}-edge`, bg, kind: "ui", why: `${t} stamp edge on the page` })),
  ]),
  { fg: "focus-ring", bg: "focus-halo", kind: "ui", why: "ring against its own halo" },
  { fg: "on-accent", bg: "accent", kind: "text", why: "primary button label" },
  { fg: "on-accent", bg: "accent-hover", kind: "text", why: "primary button label, hovered" },
  { fg: "on-danger", bg: "danger", kind: "text", why: "destructive button label" },
  { fg: "on-danger", bg: "danger-hover", kind: "text", why: "destructive button label, hovered" },
  { fg: "text-inverse", bg: "surface-inverse", kind: "text", why: "toasts, mock-mode ribbon" },
  { fg: "on-highlight", bg: "highlight", kind: "text", why: "marked text, selected rows" },
  ...TONES.map((t): Pair => ({ fg: `stamp-${t}-ink`, bg: `stamp-${t}-surface`, kind: "text", why: `${t} stamp label` })),
  // Callouts carry ordinary text and links on a tinted surface with a tone-coloured edge.
  ...TONES.flatMap((t): Pair[] => [
    { fg: "text-primary", bg: `callout-${t}-surface`, kind: "text", why: `callout text on ${t}` },
    { fg: "text-link", bg: `callout-${t}-surface`, kind: "text", why: `callout link on ${t}` },
    { fg: "danger", bg: `callout-${t}-surface`, kind: "text", why: `field errors inside a ${t} callout` },
    { fg: `callout-${t}-edge`, bg: `callout-${t}-surface`, kind: "ui", why: `callout edge on ${t}` },
    { fg: `callout-${t}-edge`, bg: "surface-page", kind: "ui", why: `callout edge on the page (${t})` },
  ]),
  { fg: "text-primary", bg: "highlight", kind: "text", why: "text on selected grid rows" },
  ...(["chart-released", "chart-window", "chart-held", "chart-invoiced"] as const).map((fg): Pair => ({ fg, bg: "surface-sheet", kind: "ui", why: `${fg} bar on the chart surface` })),
];

/** Minimum ratio per kind and theme. The contrast theme holds text to AAA (7:1) and UI to 4.5:1. */
export function minimum(kind: PairKind, theme: Theme): number {
  if (theme === "contrast") return kind === "text" ? 7 : 4.5;
  return kind === "text" ? 4.5 : 3;
}
