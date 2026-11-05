import { useState } from "react";
import type { Criterion, EvidenceCreate, EvidenceRead, EvidenceType, EvidenceVisibility, VerdictRead } from "@/api/types";
import { download } from "@/api/client";
import { errorMessage } from "@/api/errors";
import { date, dateTime, EVIDENCE_LABEL, shortHash, VISIBILITY_LABEL, VISIBILITY_ORDER } from "@/domain/format";
import {
  Button,
  Callout,
  DateField,
  ErrorSummary,
  FileDropZone,
  Glyph,
  layout,
  RadioGroup,
  Select,
  Sheet,
  Stamp,
  TextArea,
  TextField,
  fileSize,
  toast,
  type Problem,
} from "@/design/components";
import { Link } from "react-router";
import { HandoverButton, HandoverStatus, isCommitPinned, type HandoverRecord } from "./Handover";
import { Results } from "./Verdict";
import s from "./Tranche.module.css";

const SOURCE: Record<string, string> = { api: "Submitted", upload: "Uploaded", github: "GitHub merge", mcp: "Agent (MCP)", precheck: "Pre-check" };

async function fetchOriginal(e: EvidenceRead, orgId: string) {
  try {
    const { matches } = await download(`/evidence/${e.id}/file`, e.title, orgId);
    if (matches === true) toast("Matches what was verified", { body: `${e.title} hashes to the fingerprint the verifier read.`, tone: "success" });
    else if (matches === false) toast("This file doesn't match what was verified", { body: "Don't rely on it; contact the payee or support.", tone: "error" });
  } catch (err) {
    toast("Download failed", { body: errorMessage(err), tone: "error" });
  }
}

async function fetchPreview(e: EvidenceRead, orgId: string) {
  try {
    await download(`/evidence/${e.id}/preview`, `preview-${e.title}`, orgId);
  } catch (err) {
    toast("Couldn't open the preview", { body: errorMessage(err), tone: "error" });
  }
}

/** Evidence on file, numbered so verdict citations ([e1]) can point at it. */
export function EvidenceList({
  evidence, orgId, handovers = {}, checkHandover, repos,
}: {
  evidence: EvidenceRead[];
  orgId: string;
  handovers?: Record<string, HandoverRecord>;
  checkHandover?: Parameters<typeof HandoverButton>[0]["check"];
  /** Repos the viewer's GitHub App installations can read, offered in the handover check. */
  repos?: string[];
}) {
  if (!evidence.length) return <p style={{ color: "var(--text-secondary)" }}>No evidence on file yet.</p>;
  return (
    <ol className={s.evidence} aria-label="Evidence on file">
      {evidence.map((e, i) => (
        <li key={e.id} id={`ev-${e.id}`} className={s.evItem} tabIndex={-1}>
          <span className={s.evNo}>e{i + 1}</span>
          <div style={{ display: "grid", gap: "var(--space-1)", minWidth: 0 }}>
            <p className={s.evTitle}>{e.title}</p>
            <p className={s.evMeta}>
              <span>{EVIDENCE_LABEL[e.evidence_type]}</span>
              <span>{SOURCE[e.source] ?? e.source}</span>
              <span>Received {dateTime(e.received_at)}</span>
              {e.dated && <span>Dated {date(e.dated)}</span>}
              <span title={e.file_sha256 ? `sha256:${e.file_sha256}` : e.pinned_ref}>
                Fingerprint <code>{shortHash(e.file_sha256 ? `sha256:${e.file_sha256}` : e.pinned_ref, 8, 4)}</code>
              </span>
            </p>
            {e.withheld && (
              <p className={s.evMeta}>
                <Stamp flat spec={{ label: "Opens once paid", tone: "neutral", glyph: "ring", meaning: "The payee's work opens to you when this tranche is captured; until then you see the verdict and this fingerprint." }} />
                <span>You see: {VISIBILITY_LABEL[e.payer_visibility].label.toLowerCase()}</span>
              </p>
            )}
            {Object.keys(e.check_results).length > 0 && (
              <div className={s.checks} aria-label="Checks run at intake">
                {Object.entries(e.check_results).map(([k, ok]) => (
                  <Stamp key={k} flat spec={{ label: `${k} ${ok ? "pass" : "fail"}`, tone: ok ? "settled" : "alert", glyph: ok ? "check" : "cross", meaning: `Intake check ${k} ${ok ? "passed" : "failed"}.` }} />
                ))}
              </div>
            )}
            {isCommitPinned(e) && <HandoverStatus record={handovers[e.id]} />}
            {e.preview_url && e.preview_expires_at && (
              <p className={s.evMeta}>Preview link given by the payee, not checked; shown until {dateTime(e.preview_expires_at)}</p>
            )}
            {e.excerpt && <pre className={s.excerpt} aria-label={`First lines of ${e.title}`}>{e.excerpt}</pre>}
            <div className={layout.row}>
              {e.has_file && !e.withheld && (
                <Button size="sm" icon="download" onPress={() => fetchOriginal(e, orgId)}>
                  Download original<span className="sr-only">: {e.title}</span>
                </Button>
              )}
              {e.has_preview && (
                <Button size="sm" icon="download" onPress={() => fetchPreview(e, orgId)}>
                  Open preview<span className="sr-only">: {e.title}</span>
                </Button>
              )}
              {e.preview_url && !e.artifact_uris.includes(e.preview_url) && (
                <a href={e.preview_url} target="_blank" rel="noreferrer noopener">
                  Try the payee's preview<span className="sr-only"> (opens in a new tab)</span>
                </a>
              )}
              {checkHandover && isCommitPinned(e) && <HandoverButton evidence={e} check={checkHandover} repos={repos} />}
              {e.artifact_uris.map((u) => (
                <a key={u} href={u} target="_blank" rel="noreferrer noopener">{new URL(u, location.origin).host}<span className="sr-only"> (opens in a new tab)</span></a>
              ))}
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}

type Entry = { key: string; kind: "written"; data: EvidenceCreate } | { key: string; kind: "file"; file: File; text?: string };

const UPLOAD_TYPES = [".pdf", ".csv", ".xlsx", ".txt", ".md", ".png", ".jpg", ".jpeg", ".webp", "application/pdf", "text/csv", "text/plain", "text/markdown", "image/png", "image/jpeg", "image/webp", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"];
const TEXTUAL = /\.(csv|txt|md)$/i;
const MAX_FILES = 10;
const MAX_FILE = 20 * 1024 * 1024;
const MAX_TOTAL = 25 * 1024 * 1024;
const TYPES = (Object.keys(EVIDENCE_LABEL) as EvidenceType[]).map((id) => ({ id, label: EVIDENCE_LABEL[id] }));
const guessType = (name: string): EvidenceType => (/\.(csv|xlsx)$/i.test(name) ? "spreadsheet" : /\.(png|jpe?g|webp)$/i.test(name) ? "photo" : "document");

/**
 * Build a basket of evidence (files and written entries), pre-check it against the criteria without
 * submitting, then submit. Pre-check reads text files in the browser; PDFs and spreadsheets are checked
 * after upload.
 */
export function AddEvidence({
  criteria, mock, canSubmit, submitReason, onSubmitted, actions, minVisibility,
}: {
  criteria: Criterion[];
  /** Set when the agreement withholds work until capture: the least the payer may see of each item. */
  minVisibility?: EvidenceVisibility;
  mock: boolean;
  canSubmit: boolean;
  submitReason?: string;
  onSubmitted: () => void;
  actions: {
    submit: { mutateAsync: (b: EvidenceCreate) => Promise<unknown>; isPending: boolean };
    upload: { mutateAsync: (b: { files: File[]; note?: string; dated?: string; payer_visibility?: EvidenceVisibility; preview_pages?: string }) => Promise<unknown>; isPending: boolean };
    precheck: { mutateAsync: (e: EvidenceCreate[]) => Promise<VerdictRead>; isPending: boolean; data?: VerdictRead; reset: () => void };
  };
}) {
  const [basket, setBasket] = useState<Entry[]>([]);
  const [note, setNote] = useState("");
  const [dated, setDated] = useState<string | null>(null);
  const [problems, setProblems] = useState<Problem[]>([]);
  const [attempt, setAttempt] = useState(0);
  const [writing, setWriting] = useState(false);
  const [draft, setDraft] = useState<EvidenceCreate>({ evidence_type: "document", title: "", content: "" });
  const [precheck, setPrecheck] = useState<{ verdict: VerdictRead; skipped: string[] } | null>(null);
  const [busy, setBusy] = useState<"precheck" | "submit" | null>(null);
  const [visibility, setVisibility] = useState<EvidenceVisibility | undefined>(minVisibility);
  const shown = minVisibility ? visibility : undefined;
  const [previewPages, setPreviewPages] = useState("");
  const pagesError = previewPages.trim() && !/^\s*\d+(\s*-\s*\d+)?(\s*,\s*\d+(\s*-\s*\d+)?)*\s*$/.test(previewPages) ? "Pages look like 1-2,5" : undefined;

  const files = basket.filter((b): b is Extract<Entry, { kind: "file" }> => b.kind === "file");
  const total = files.reduce((n, f) => n + f.file.size, 0);

  async function addFiles(list: File[]) {
    const p: Problem[] = [];
    const next: Entry[] = [];
    for (const f of list) {
      if (f.size > MAX_FILE) p.push({ message: `${f.name} is ${fileSize(f.size)}; files can be up to 20 MB` });
      else next.push({ key: `${f.name}-${f.size}-${Math.random()}`, kind: "file", file: f, text: TEXTUAL.test(f.name) ? await f.text() : undefined });
    }
    if (files.length + next.length > MAX_FILES) p.push({ message: `Up to ${MAX_FILES} files per submission` });
    if (total + next.reduce((n, e) => n + (e.kind === "file" ? e.file.size : 0), 0) > MAX_TOTAL) p.push({ message: "Up to 25 MB of files per submission" });
    setProblems(p);
    if (!p.length) setBasket((b) => [...b, ...next]);
    setPrecheck(null);
  }

  function addWritten() {
    const p: Problem[] = [];
    if (!draft.title.trim()) p.push({ message: "Give the entry a title", href: "#ev-title" });
    if (!draft.content.trim()) p.push({ message: "Paste or write the evidence text", href: "#ev-content" });
    setProblems(p);
    setAttempt((n) => n + 1);
    if (p.length) return;
    setBasket((b) => [...b, { key: `w${Date.now()}`, kind: "written", data: { ...draft, title: draft.title.trim() } }]);
    setDraft({ evidence_type: "document", title: "", content: "" });
    setWriting(false);
    setPrecheck(null);
  }

  function asDrafts(): { items: EvidenceCreate[]; skipped: string[] } {
    const items: EvidenceCreate[] = [];
    const skipped: string[] = [];
    for (const b of basket) {
      if (b.kind === "written") items.push({ ...b.data, dated: b.data.dated ?? dated ?? undefined });
      else if (b.text !== undefined) items.push({ evidence_type: guessType(b.file.name), title: b.file.name, content: b.text, dated: dated ?? undefined });
      else skipped.push(b.file.name);
    }
    if (note.trim()) items.push({ evidence_type: "document", title: "Payee note", content: note.trim() });
    return { items, skipped };
  }

  async function runPrecheck() {
    const { items, skipped } = asDrafts();
    if (!items.length) {
      setProblems([{ message: "Pre-check reads written entries and CSV, TXT or MD files. Add one of those first." }]);
      return;
    }
    setBusy("precheck");
    try {
      const v = await actions.precheck.mutateAsync(items);
      setPrecheck({ verdict: v, skipped });
      toast(v.met ? "Pre-check: everything looks met" : "Pre-check: something's missing", { body: "Nothing was submitted.", tone: v.met ? "success" : "info" });
    } catch (e) {
      setProblems([{ message: errorMessage(e) }]);
    } finally {
      setBusy(null);
    }
  }

  async function submit() {
    if (!basket.length) {
      setProblems([{ message: "Add at least one file or written entry" }]);
      return;
    }
    setBusy("submit");
    try {
      const written = basket.filter((b) => b.kind === "written");
      for (const w of written) await actions.submit.mutateAsync({ ...w.data, dated: w.data.dated ?? dated ?? undefined, payer_visibility: shown });
      if (files.length || note.trim()) {
        if (files.length) await actions.upload.mutateAsync({ files: files.map((f) => f.file), note: note.trim() || undefined, dated: dated ?? undefined, payer_visibility: shown, preview_pages: shown === "preview" && previewPages.trim() ? previewPages.trim() : undefined });
        else await actions.submit.mutateAsync({ evidence_type: "document", title: "Payee note", content: note.trim(), payer_visibility: shown });
      }
      toast("Evidence submitted", { body: "Checks run first, then the verifier. This page updates on its own." });
      setBasket([]);
      setNote("");
      setPrecheck(null);
      onSubmitted();
    } catch (e) {
      setProblems([{ message: errorMessage(e) }]);
    } finally {
      setBusy(null);
    }
  }


  return (
    <Sheet title="Add evidence" id="add-evidence">
      <div className={layout.stack}>
        <ErrorSummary problems={problems} focusKey={attempt} title="Check the evidence" />
        <FileDropZone
          label="Drop files"
          hint="PDF, CSV, XLSX, TXT, Markdown or images. Up to 10 files, 20 MB each, 25 MB in total. Spreadsheets are read sheet by sheet; PDFs are read as text and the original kept."
          accept={UPLOAD_TYPES}
          multiple
          files={[]}
          onFiles={addFiles}
        />
        {writing ? (
          <fieldset style={{ border: "1px solid var(--rule-hairline)", padding: "var(--space-4)", margin: 0 }} className={layout.stack}>
            <legend style={{ fontWeight: 700, padding: "0 var(--space-2)" }}>Written entry</legend>
            <div className={layout.grid2}>
              <TextField id="ev-title" label="Title" value={draft.title} onChange={(v) => setDraft({ ...draft, title: v })} error={problems.find((p) => p.href === "#ev-title")?.message} />
              <Select label="Kind" value={draft.evidence_type} options={TYPES} onChange={(v) => setDraft({ ...draft, evidence_type: v })} />
            </div>
            <TextArea id="ev-content" label="Evidence text" description="Paste the report text, a table as CSV, or a statement. It's treated as evidence, never as instructions." value={draft.content} onChange={(v) => setDraft({ ...draft, content: v })} rows={6} error={problems.find((p) => p.href === "#ev-content")?.message} />
            <TextField label="Link" optional type="url" description="A public URL: a published page, video or listing." value={draft.artifact_uris?.[0] ?? ""} onChange={(v) => setDraft({ ...draft, artifact_uris: v ? [v] : [] })} />
            <div className={layout.row}>
              <Button variant="primary" icon="plus" onPress={addWritten}>Add to basket</Button>
              <Button variant="quiet" onPress={() => setWriting(false)}>Cancel</Button>
            </div>
          </fieldset>
        ) : (
          <div className={layout.row}>
            <Button icon="edit" onPress={() => setWriting(true)}>Write an entry</Button>
            {mock && <Link to="/demo" style={{ fontSize: "var(--text-sm)" }}>Sample evidence files are on the demo page</Link>}
          </div>
        )}

        <section aria-labelledby="basket-h" className={layout.stack} style={{ gap: "var(--space-2)" }}>
          <h3 id="basket-h" style={{ fontSize: "var(--text-lg)", margin: 0 }}>Basket ({basket.length})</h3>
          {basket.length === 0 ? (
            <p style={{ color: "var(--text-secondary)", margin: 0 }}>Nothing added yet.</p>
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {basket.map((b) => (
                <li key={b.key} className={layout.row} style={{ justifyContent: "space-between", borderBottom: "1px solid var(--rule-hairline)", padding: "var(--space-1) 0", margin: 0 }}>
                  <span className={layout.row}>
                    <Glyph name={b.kind === "file" ? "file" : "edit"} />
                    <span>{b.kind === "file" ? b.file.name : b.data.title}</span>
                    <span style={{ color: "var(--text-secondary)", fontSize: "var(--text-sm)" }}>
                      {b.kind === "file" ? `${fileSize(b.file.size)}${b.text === undefined ? " · checked after upload" : ""}` : EVIDENCE_LABEL[b.data.evidence_type]}
                    </span>
                  </span>
                  <Button size="sm" variant="quiet" icon="close" aria-label={`Remove ${b.kind === "file" ? b.file.name : b.data.title}`} onPress={() => { setBasket((x) => x.filter((y) => y.key !== b.key)); setPrecheck(null); }} />
                </li>
              ))}
            </ul>
          )}
        </section>

        <div className={layout.grid2}>
          <TextArea label="Note to the reviewer" optional value={note} onChange={setNote} rows={2} maxLength={2000} />
          <DateField label="Evidence date" optional description="If the files don't carry their own date (used by date checks)." value={dated} onChange={setDated} />
        </div>

        {minVisibility && (
          <RadioGroup
            label="What the payer sees before paying"
            description="This agreement keeps your work from the payer until the tranche is paid. Choose how much of the verdict they see first; the agreement sets the minimum."
            value={visibility ?? minVisibility}
            onChange={(v) => setVisibility(v as EvidenceVisibility)}
            options={VISIBILITY_ORDER.slice(VISIBILITY_ORDER.indexOf(minVisibility)).map((id) => ({ id, ...VISIBILITY_LABEL[id] }))}
          />
        )}
        {shown === "preview" && basket.some((b) => b.kind === "file" && /\.pdf$/i.test(b.file.name)) && (
          <TextField label="PDF pages in the preview" optional placeholder="1-2" description="Up to 5 pages, like 1-2,5. The first two if left empty. Use the evidence tab to check what the payer will see." value={previewPages} onChange={setPreviewPages} error={pagesError} figures />
        )}

        <div className={layout.row}>
          <Button icon="search" onPress={runPrecheck} isPending={busy === "precheck"} isDisabled={!basket.length && !note.trim()}>Pre-check without submitting</Button>
          <Button variant="primary" icon="upload" onPress={submit} isPending={busy === "submit"} isDisabled={!canSubmit || !basket.length}>Submit evidence</Button>
        </div>
        {!canSubmit && submitReason && <p style={{ color: "var(--text-secondary)", fontSize: "var(--text-sm)", margin: 0 }}>{submitReason}</p>}

        {precheck && (
          <section aria-labelledby="pc-h" className={layout.stack} style={{ borderTop: "2px dashed var(--rule-strong)", paddingTop: "var(--space-4)" }}>
            <div className={layout.row}>
              <h3 id="pc-h" style={{ margin: 0 }}>Pre-check result</h3>
              <Stamp spec={{ label: "Not submitted", tone: "neutral", glyph: "ring", meaning: "A pre-check records nothing and moves no money." }} />
            </div>
            <Callout tone={precheck.verdict.met ? "success" : "warning"} title={precheck.verdict.met ? "This evidence looks like it meets every criterion" : "Not everything is met yet"} live>
              <p>{precheck.verdict.rationale}</p>
              {precheck.skipped.length > 0 && <p>Not read in the pre-check (checked after upload): {precheck.skipped.join(", ")}.</p>}
            </Callout>
            <Results criteria={criteria} results={precheck.verdict.criteria_results} evidence={[]} />
          </section>
        )}
      </div>
    </Sheet>
  );
}
