# Ledger & Stamp: the design system

The app looks like a well-kept account book and case file. It uses paper, ink, ruled lines, stamps, signatures and footnotes. Every motif stands for something real in the domain, which keeps it specific instead of decorative:

| Motif | Means | Component |
| --- | --- | --- |
| Ink stamp | A status someone or something decided | `Stamp` |
| Accounting rules (single above, double below) | A total | `Totals`, ledger footers |
| Wax seal | The criteria hash everyone signs | `Seal` |
| Signature line | A party signed this exact version | `SignatureBlock` |
| Marginal notes | Lint findings and the source quote for a criterion | Marginalia in `TranchesEditor` |
| Footnotes `[e1]` | A verdict citing its evidence | `Citations` |
| `№` numerals | Tranches in payment order | Ledger rows |

The live reference is `/app/design`. It shows every token, a contrast matrix measured from what the browser is rendering, and every component.

## Rules that keep it from looking templated

- **No:**
  - gradients or glass
  - glows or blurred shadows (elevation is an offset "sheet" shadow and a rule)
  - pill shapes or big rounded cards (radius is 2px everywhere)
  - emoji or third-party icon packs (`Glyph` is a small square-capped stroke set drawn to match the stamps)
- **One accent ink per theme.** Status colour comes only from the five stamp inks, and they mean the same thing everywhere:

  | Ink | Means |
  | --- | --- |
  | green | settled |
  | blue | held / in progress |
  | ochre | needs a person |
  | vermilion | failed / disputed |
  | graphite | neutral |
- **Type:**
  - **Newsreader** for display
  - **Atkinson Hyperlegible Next** for UI; it was designed for low-vision readers
  - **Atkinson Hyperlegible Mono** for money, hashes and ids, always with tabular figures

  All three are self-hosted, with no font CDN.
- **Copy is plain and specific, and reuses the API's own words.**
  - Write "Payee hasn't signed these criteria yet", never "Oops!".
  - Empty states are a sentence of writing, not an illustration.
- **Motion:** the stamp "press" when a status changes, and the dialog slide. Nothing else moves. Both are off under `prefers-reduced-motion`.

## Colour is computed, not eyeballed

`src/design/palette.ts` is the only place colours are defined, in OKLCH, for three themes:

- **Paper** (light)
- **Carbon** (dark)
- **Contrast** (black on white; 7:1 text, 4.5:1 UI)

`npm run tokens` writes `src/design/themes.css` from it and checks every pair in `PAIRS`. `npm run tokens:check` fails CI if the CSS is stale or any pair misses its minimum:

| Pair | Minimum |
| --- | --- |
| Text | 4.5:1 (WCAG 1.4.3) |
| UI boundaries, focus rings, stamp edges | 3:1 (WCAG 1.4.11) |

Currently 291/291 pass. A new colour isn't usable until its pairs are declared, and `palette.test.ts` runs the same check in the unit tests.

Token rules learned the hard way:
- **Stamp inks** (`--stamp-*-ink`) are for text *on their own stamp surface*. In Contrast the stamps are solid fills with white ink, so a stamp ink used as plain text disappears. For "done/passed" text on a page use `--text-success`.
- **Callouts** have their own `--callout-*` surfaces and edges; they don't reuse stamp surfaces.
- **`--rule-hairline` is decorative only.** Anything a person has to see as a boundary uses `--border-control` or `--rule-strong`.
- **Chart series** (`--chart-*`: paid out, in dispute window, held, invoiced, in that stacking order) were run through the dataviz palette validator (lightness band, chroma floor, colour-blind and normal-vision separation of adjacent pairs) in Paper and Carbon. They're also held to 3:1 against the chart surface by the token check. Unfunded money is a dashed neutral track, not a fifth hue.

## Accessibility contract (WCAG 2.2 AA)

- **Structure:** a skip link and landmarks on every page. On navigation, focus moves to the new page's `h1` and the title is announced (`RouteAnnouncer`).
- **One shared pair of live regions** (`Announcer`) for status messages. Toasts are announced there, stay at least 8 seconds, pause on hover or focus, and are never the only place an outcome appears.
- **Focus:**
  - Every interactive element shows a 2px ring with a halo, at 3:1 in every theme.
  - In forced-colors mode it uses the system `Highlight`.
  - The header stops being sticky on short or narrow screens, so it can't cover what has focus (2.4.11).
- **Targets** are 44px by default and never below 24px (2.5.8).
- **Behaviour:** interactive components come from React Aria (keyboard, focus management, ARIA, typeahead, focus trapping and return). Visuals are entirely ours.
- **Forms:**
  - visible labels
  - descriptions and errors wired with `aria-describedby`
  - an `ErrorSummary` that takes focus and links to each field
  - server validation and lint paths (`milestones[0].criteria[1].statement`) mapped onto the exact input
- **Status is never colour alone.** Stamps carry a word and a distinct glyph, the confidence meter is hatched below the threshold, and verdict marks are ✓ / ✕ / ○.
- **Disabled actions stay visible** with a `PermissionNote` saying which role is needed and which you have.
- **Money-moving or irreversible actions** use a confirm `alertdialog` that states exactly what will happen ("Release USD 10,000.00 to Riverside Reads").
- **Grids** (AG Grid) are named by a visible caption and described by a one-line summary. Enter follows a link in a cell.
- **Charts** have a text summary and a "Show as table" switch, and the canvas is `inert`.
- **Reflow:** pages reflow at 320px with no horizontal scrolling outside grids (1.4.10). Long emails and hashes wrap.

### How it's verified

| Check | Where |
| --- | --- |
| Contrast of every declared pair, three themes | `npm run tokens:check`, `palette.test.ts` |
| Component semantics with axe in jsdom | `src/design/components/components.test.tsx` |
| Permission rules pinned to the spec's table | `src/domain/permissions.test.ts` |
| axe (WCAG 2.0–2.2 A/AA) on every route × 3 themes | `e2e/a11y.spec.ts` |
| Forced colors + 320px reflow on every route | `e2e/a11y.spec.ts` |
| Keyboard-only signing, focus trap and focus return | `e2e/a11y.spec.ts` |
| Full money flows with no console errors | `e2e/grant-escrow.spec.ts`, `e2e/flows.spec.ts` |

Still manual: screen-reader passes (VoiceOver/NVDA) on the tranche page and the review dialog, and zoom to 400%.

## Security notes

- **Session tokens** live in `localStorage` ("keep me signed in") or `sessionStorage`, so any script on the page could read them. The app loads no third-party scripts or fonts. In production, serve it with a strict CSP (`script-src 'self'`).
- **Invitation and sign-in tokens** are removed from the address bar as soon as they're read.
- **Evidence text** is rendered as text, never HTML.
