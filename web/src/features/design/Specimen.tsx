import { useState } from "react";
import { useTitle } from "@/app/RouteAnnouncer";
import { PAIRS, PALETTE, minimum, type Theme } from "@/design/palette";
import { contrast } from "@/design/contrast";
import { useCssVars } from "@/design/useCssVars";
import { useTheme, THEME_OPTIONS, setTheme, type ThemeChoice } from "@/design/theme";
import { AGREEMENT_STAMPS, INVITATION_STAMPS, MILESTONE_STAMPS, SETTLEMENT_STAMPS } from "@/domain/status";
import {
  Amount, Button, Callout, Checkbox, ConfirmDialog, CopyButton, DateField, Disclosure, ErrorSummary, Glyph, layout, NumberField,
  PageHeader, PermissionNote, RadioGroup, Seal, Select, Sheet, Stamp, Switch, Tabs, TextArea, TextField, ThresholdMeter, Totals, toast, type GlyphName,
} from "@/design/components";
import s from "./Specimen.module.css";

const TOKENS = Object.keys(PALETTE.paper) as (keyof (typeof PALETTE)["paper"])[];

/**
 * The design system on one page: colour tokens with a contrast matrix measured from what the browser is
 * actually rendering, the type scale, and every component in its states. It's the docs and an axe target.
 */
export function Specimen() {
  useTitle("Design system");
  const theme = useTheme();
  const vars = useCssVars(TOKENS);
  const effective: Theme = theme === "system" ? (document.documentElement.dataset.theme as Theme) ?? (matchMedia("(prefers-contrast: more)").matches ? "contrast" : matchMedia("(prefers-color-scheme: dark)").matches ? "carbon" : "paper") : theme;
  const [dialog, setDialog] = useState(false);
  const [text, setText] = useState("");
  const [num, setNum] = useState<number | null>(20000);
  const [day, setDay] = useState<string | null>("2027-05-31");
  const [sel, setSel] = useState<"a" | "b">("a");
  const [on, setOn] = useState(true);
  const results = PAIRS.map((p) => {
    const ratio = vars[p.fg] && vars[p.bg] ? contrast(vars[p.fg], vars[p.bg]) : 0;
    return { ...p, ratio, min: minimum(p.kind, effective), ok: ratio >= minimum(p.kind, effective) };
  });
  const failing = results.filter((r) => !r.ok).length;

  return (
    <div className={layout.stackLg}>
      <PageHeader
        eyebrow="Ledger & Stamp"
        title="Design system"
        lede="Paper, ink, rules, stamps, signatures and footnotes. Every colour pair is checked against WCAG 2.2 at build time; the matrix below re-measures what your browser is showing right now."
      />
      <Sheet title="Theme">
        <RadioGroup label="Preview theme" orientation="horizontal" value={theme} onChange={(v) => setTheme(v as ThemeChoice)} options={THEME_OPTIONS.map((t) => ({ id: t.id, label: t.label }))} />
      </Sheet>

      <Sheet title="Colour tokens">
        <div className={s.swatches}>
          {TOKENS.map((t) => (
            <div key={t} className={s.swatch}>
              <div className={s.chip} style={{ background: `var(--${t})` }} aria-hidden="true" />
              <div className={s.swatchText}><strong>--{t}</strong><code>{vars[t]}</code></div>
            </div>
          ))}
        </div>
      </Sheet>

      <Sheet title={`Contrast: ${results.length - failing} of ${results.length} pairs pass in ${effective}`}>
        <table className={s.matrix}>
          <caption className="sr-only">Contrast ratio of each declared colour pair in the current theme</caption>
          <thead><tr><th scope="col">Sample</th><th scope="col">Foreground</th><th scope="col">Background</th><th scope="col">Use</th><th scope="col">Ratio</th><th scope="col">Needs</th><th scope="col">Result</th></tr></thead>
          <tbody>
            {results.map((r, i) => (
              <tr key={i}>
                <td>{r.kind === "text" ? <span className={s.sample} style={{ color: `var(--${r.fg})`, background: `var(--${r.bg})` }}>Aa</span> : <span className={s.sample} aria-hidden="true" style={{ background: `var(--${r.bg})` }}><span style={{ display: "inline-block", width: "1.5rem", height: "0.9rem", verticalAlign: "middle", border: `2px solid var(--${r.fg})` }} /></span>}</td>
                <td><code>{r.fg}</code></td>
                <td><code>{r.bg}</code></td>
                <td>{r.why}</td>
                <td className="figures">{r.ratio.toFixed(2)}:1</td>
                <td className="figures">{r.min}:1</td>
                <td className={r.ok ? s.pass : s.fail}>{r.ok ? "Pass" : "Fail"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Sheet>

      <Sheet title="Type">
        {[
          ["Display 3xl", "var(--text-3xl)", "var(--font-display)", "Riverside Literacy Grant"],
          ["Display 2xl", "var(--text-2xl)", "var(--font-display)", "Tranche 1: spring workshops"],
          ["Display xl", "var(--text-xl)", "var(--font-display)", "Signatures"],
          ["UI lg", "var(--text-lg)", "var(--font-ui)", "At least 200 people attend the spring reading workshops"],
          ["UI md", "var(--text-md)", "var(--font-ui)", "Body copy is never smaller than 16px."],
          ["UI sm", "var(--text-sm)", "var(--font-ui)", "Secondary text and descriptions."],
          ["Figures", "var(--text-md)", "var(--font-figures)", "USD 20,000.00 · 9f2c41…e41a · 0123456789"],
        ].map(([n, size, fam, sample]) => (
          <div key={n} className={s.typeRow}><code>{n}</code><span style={{ fontSize: size, fontFamily: fam }}>{sample}</span></div>
        ))}
      </Sheet>

      <Sheet title="Stamps">
        <div className={layout.stack}>
          {[["Tranche", MILESTONE_STAMPS], ["Agreement", AGREEMENT_STAMPS], ["Settlement", SETTLEMENT_STAMPS], ["Invitation", INVITATION_STAMPS]].map(([label, map]) => (
            <div key={label as string}>
              <h3 style={{ fontSize: "var(--text-md)", fontFamily: "var(--font-ui)" }}>{label as string}</h3>
              <div className={s.gallery}>{Object.values(map as Record<string, typeof MILESTONE_STAMPS.pending>).map((sp) => <Stamp key={sp.label} spec={sp} />)}</div>
            </div>
          ))}
        </div>
      </Sheet>

      <Sheet title="Buttons">
        <div className={s.gallery}>
          <Button variant="primary" icon="check">Primary</Button>
          <Button>Secondary</Button>
          <Button variant="quiet">Quiet</Button>
          <Button variant="danger" icon="trash">Danger</Button>
          <Button variant="primary" isPending>Pending</Button>
          <Button isDisabled>Disabled</Button>
          <Button size="sm" icon="plus">Small</Button>
          <Button icon="copy" aria-label="Icon only" />
          <CopyButton value="example" label="Copy example" />
          <Button onPress={() => toast("Saved", { body: "A toast is announced politely and stays at least 8 seconds." })}>Show a toast</Button>
          <Button onPress={() => setDialog(true)}>Open a confirm dialog</Button>
        </div>
      </Sheet>

      <Sheet title="Fields">
        <div className={layout.grid2}>
          <TextField label="Text" description="With a description." value={text} onChange={setText} />
          <TextField label="With an error" value="" onChange={() => undefined} error="Enter an email address like name@example.org" />
          <NumberField label="Money" prefix="USD" fractionDigits={2} value={num} onChange={setNum} />
          <DateField label="Date" value={day} onChange={setDay} />
          <Select label="Select" value={sel} onChange={setSel} options={[{ id: "a", label: "Escrow release", description: "Hold, then capture" }, { id: "b", label: "Invoice on verify" }]} />
          <TextArea label="Text area" optional value={text} onChange={setText} maxLength={200} />
          <RadioGroup label="Radio" value={sel} onChange={setSel} options={[{ id: "a", label: "First", description: "A hint" }, { id: "b", label: "Second" }]} />
          <div className={layout.stack}>
            <Checkbox isSelected={on} onChange={setOn} hint="With a hint">Checkbox</Checkbox>
            <Switch isSelected={on} onChange={setOn}>Switch</Switch>
          </div>
        </div>
      </Sheet>

      <Sheet title="Feedback">
        <div className={layout.stack}>
          <ErrorSummary problems={[{ message: "Enter the agreement total", href: "#nowhere" }, { message: "Write the condition a reviewer will check", path: "milestones[0].criteria[1].statement" }]} focusKey="never" />
          <Callout tone="info" title="Info">Neutral information.</Callout>
          <Callout tone="success" title="Success">Something completed.</Callout>
          <Callout tone="warning" title="Warning">Needs attention.</Callout>
          <Callout tone="error" title="Error">Something failed.</Callout>
          <Callout tone="note" title="Note">An aside.</Callout>
          <PermissionNote>Releasing needs admin in Harbor Foundation; you're a member.</PermissionNote>
          <ThresholdMeter label="Verifier confidence" value={0.72} threshold={0.85} />
          <ThresholdMeter label="Verifier confidence" value={0.93} threshold={0.85} />
        </div>
      </Sheet>

      <Sheet title="Ledger pieces">
        <div className={layout.grid2}>
          <Totals label="Example totals" currency="USD" lines={[{ label: "Released", value: "10000.00" }, { label: "Held", value: "20000.00" }]} total={{ label: "Total", value: "30000.00" }} />
          <div className={layout.stack}>
            <Seal hash="9f2c41d8a0b7e6c5d4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3e41a" />
            <Seal hash="9f2c41d8a0b7e6c5d4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3e41a" locked label="Locked criteria" />
            <p>A figure inline: <Amount value="20000" currency="USD" /></p>
          </div>
        </div>
        <Disclosure title="Disclosure">Hidden detail.</Disclosure>
        <Tabs label="Example tabs" tabs={[{ id: "one", label: "One", content: <p>First panel.</p> }, { id: "two", label: "Two", content: <p>Second panel.</p> }]} />
      </Sheet>

      <Sheet title="Glyphs">
        <div className={s.gallery}>
          {(["check", "half", "ring", "cross", "pause", "clock", "dot", "arrow", "plus", "copy", "download", "upload", "external", "warning", "info", "signature", "key", "person", "org", "file", "link", "github", "trash", "edit", "search", "ledger", "stamp", "inbox", "refresh"] as GlyphName[]).map((g) => (
            <span key={g} title={g} style={{ display: "inline-grid", placeItems: "center", width: 40, height: 40, border: "1px solid var(--rule-hairline)" }}><Glyph name={g} size={20} label={g} /></span>
          ))}
        </div>
      </Sheet>

      <ConfirmDialog isOpen={dialog} onOpenChange={setDialog} title="Release this payment?" summary={<p style={{ margin: 0 }}>Capture the hold and pay <strong>USD 10,000.00</strong> to Riverside Reads.</p>} confirmLabel="Release USD 10,000.00" onConfirm={() => setDialog(false)}>
        <p>This can't be undone from here.</p>
      </ConfirmDialog>
    </div>
  );
}
