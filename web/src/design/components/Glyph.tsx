/**
 * The icon set: square-capped strokes on a 16px grid, drawn to match the stamp language (ruled, inked,
 * no rounded blobs). Decorative by default (aria-hidden); pass `label` when a glyph stands alone.
 */
import type { SVGProps } from "react";

const PATHS = {
  check: "M3 8.5l3 3L13 4.5",
  half: "M8 2.5a5.5 5.5 0 1 0 0 11zM8 2.5a5.5 5.5 0 1 1 0 11",
  ring: "M8 2.5a5.5 5.5 0 1 0 0 11a5.5 5.5 0 1 0 0-11",
  cross: "M4 4l8 8M12 4l-8 8",
  pause: "M6 3.5v9M10 3.5v9",
  clock: "M8 2.5a5.5 5.5 0 1 0 0 11a5.5 5.5 0 1 0 0-11M8 5v3.2l2.2 1.4",
  dot: "M8 6.2a1.8 1.8 0 1 0 0 3.6a1.8 1.8 0 1 0 0-3.6",
  arrow: "M2.5 8h10M9 4.5L12.5 8 9 11.5",
  plus: "M8 3v10M3 8h10",
  minus: "M3 8h10",
  copy: "M5.5 5.5h7v7h-7zM3.5 10.5v-7h7",
  download: "M8 2.5v8M4.5 7L8 10.5 11.5 7M3 13.5h10",
  upload: "M8 10.5v-8M4.5 6L8 2.5 11.5 6M3 13.5h10",
  external: "M9.5 2.5h4v4M13.5 2.5L7.5 8.5M11.5 9.5v4h-9v-9h4",
  chevronDown: "M4 6l4 4 4-4",
  chevronUp: "M4 10l4-4 4 4",
  chevronRight: "M6 4l4 4-4 4",
  chevronLeft: "M10 4L6 8l4 4",
  menu: "M2.5 4.5h11M2.5 8h11M2.5 11.5h11",
  close: "M4 4l8 8M12 4l-8 8",
  warning: "M8 2.5l6 11H2zM8 6.5v3.5M8 11.5v.5",
  info: "M8 2.5a5.5 5.5 0 1 0 0 11a5.5 5.5 0 1 0 0-11M8 7.5v4M8 5v.5",
  signature: "M2.5 12.5c2-3 3-7 4.5-7s-1 6 1 6 2-3 3-3 .5 2 2.5 2M2.5 14h11",
  key: "M10.5 2.5a3 3 0 1 0 0 6a3 3 0 1 0 0-6M8.4 7.6L2.5 13.5M4.5 11.5l1.5 1.5M6 10l1.2 1.2",
  person: "M8 2.5a2.5 2.5 0 1 0 0 5a2.5 2.5 0 1 0 0-5M3 13.5c.5-3 2.5-4.5 5-4.5s4.5 1.5 5 4.5",
  org: "M2.5 13.5h11M3.5 13.5v-7l4.5-3 4.5 3v7M6 13.5v-4h4v4",
  file: "M4 2.5h5l3 3v8H4zM9 2.5v3h3",
  link: "M7 9a2.5 2.5 0 0 0 3.5 0l2-2a2.5 2.5 0 0 0-3.5-3.5l-.7.7M9 7a2.5 2.5 0 0 0-3.5 0l-2 2A2.5 2.5 0 0 0 7 12.5l.7-.7",
  github: "M6 13.5v-2c-2.5.5-3-1-3.5-1.5M10 13.5v-2.2c0-.8-.3-1.3-.7-1.6 2-.2 3.7-1 3.7-3.7 0-.8-.3-1.5-.8-2 .1-.6.1-1.3-.2-2 0 0-.7-.2-2 .8a7 7 0 0 0-3.6 0c-1.4-1-2-.8-2-.8-.3.7-.3 1.4-.2 2-.5.5-.8 1.2-.8 2 0 2.7 1.7 3.5 3.7 3.7-.3.3-.6.8-.7 1.4",
  trash: "M3 4.5h10M6.5 4.5v-2h3v2M4.5 4.5l.7 9h5.6l.7-9",
  edit: "M10.5 2.5l3 3-8 8h-3v-3zM9 4l3 3",
  search: "M7 2.5a4.5 4.5 0 1 0 0 9a4.5 4.5 0 1 0 0-9M10.5 10.5l3 3",
  sun: "M8 5a3 3 0 1 0 0 6a3 3 0 1 0 0-6M8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15M3 3l1 1M12 12l1 1M3 13l1-1M12 4l1-1",
  moon: "M12.5 10A5.5 5.5 0 0 1 6 3.5a5.5 5.5 0 1 0 6.5 6.5z",
  contrast: "M8 2.5a5.5 5.5 0 1 0 0 11a5.5 5.5 0 1 0 0-11M8 2.5v11",
  ledger: "M3.5 2.5h9v11h-9zM3.5 5.5h9M6.5 2.5v11",
  stamp: "M5.5 2.5h5v3l-1 2h3l1 3h-11l1-3h3l-1-2zM2.5 13.5h11",
  inbox: "M2.5 9.5l2-6h7l2 6v4h-11zM2.5 9.5h3l1 1.5h3l1-1.5h3",
  refresh: "M13 5.5A5.5 5.5 0 0 0 3 6M3 2.5v3.5h3.5M3 10.5A5.5 5.5 0 0 0 13 10M13 13.5V10H9.5",
  play: "M5 3l8 5-8 5z",
  back: "M13.5 8h-10M7 4.5L3.5 8 7 11.5",
  filter: "M2.5 3.5h11l-4.5 5v4l-2 1v-5z",
  signOut: "M6.5 2.5h-4v11h4M10 5l3 3-3 3M13 8H6",
} as const;

export type GlyphName = keyof typeof PATHS;

type Props = Omit<SVGProps<SVGSVGElement>, "name"> & { name: GlyphName; size?: number; label?: string };

export function Glyph({ name, size = 16, label, ...rest }: Props) {
  const filled = name === "dot" || name === "play";
  return (
    <svg
      viewBox="0 0 16 16"
      width={size}
      height={size}
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="square"
      strokeLinejoin="miter"
      aria-hidden={label ? undefined : true}
      role={label ? "img" : undefined}
      aria-label={label}
      focusable="false"
      {...rest}
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
