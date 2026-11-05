import { useState } from "react";
import type { AuditEventRead, EvidenceRead, HandoverCheck, HandoverResult } from "@/api/types";
import { errorMessage } from "@/api/errors";
import { dateTime, shortHash } from "@/domain/format";
import { Button, Callout, Dialog, layout, RadioGroup, Select, Stamp, TextField } from "@/design/components";
import s from "./Tranche.module.css";

const SHA = /^[0-9a-f]{40}$/i;
const REPO = /^[A-Za-z0-9-]{1,39}\/[A-Za-z0-9._-]{1,100}$/;

/** Evidence pinned to a commit (GitHub merges) can be checked against what was handed over. */
export const isCommitPinned = (e: EvidenceRead) => SHA.test(e.pinned_ref);

export type HandoverRecord = { at: string; matches: boolean; how: string; delivered_sha: string; repo?: string | null; ref?: string | null };

/** The latest handover check per evidence id, from the tranche's audit events. */
export function latestHandovers(events: AuditEventRead[]): Record<string, HandoverRecord> {
  const out: Record<string, HandoverRecord> = {};
  for (const e of events) {
    if (e.action !== "handover_checked") continue;
    const d = e.detail as Record<string, unknown>;
    if (typeof d.evidence_id === "string") {
      out[d.evidence_id] = { at: e.at, matches: !!d.matches, how: String(d.how), delivered_sha: String(d.delivered_sha), repo: d.repo as string | null, ref: d.ref as string | null };
    }
  }
  return out;
}

const HOW: Record<string, string> = {
  same_commit: "Same commit as the one verified",
  same_tree: "Same files as the commit verified (a different commit, e.g. squashed into your repo)",
  different: "Not the commit that was verified, and the files differ",
};

/** One line on the evidence item: the last check's result. */
export function HandoverStatus({ record }: { record?: HandoverRecord }) {
  if (!record) return null;
  return (
    <p className={s.evMeta}>
      <Stamp
        flat
        spec={record.matches
          ? { label: "Handover matches", tone: "settled", glyph: "check", meaning: HOW[record.how] ?? "Matches what was verified." }
          : { label: "Handover differs", tone: "alert", glyph: "cross", meaning: HOW.different! }}
      />
      <span>
        {record.repo ? `${record.repo}${record.ref ? `@${record.ref}` : ""} · ` : ""}
        <code>{shortHash(record.delivered_sha, 7, 4)}</code> · checked {dateTime(record.at)}
      </span>
    </p>
  );
}

type Check = { mutateAsync: (v: { evidenceId: string; body: HandoverCheck }) => Promise<HandoverResult>; isPending: boolean };

/**
 * Check code delivered outside the engine (a PR into the client's repo, a repo transfer) against the commit the
 * verifier read. Either side or a reviewer can run it; every check is recorded in the history and the packet.
 */
export function HandoverButton({ evidence, check, repos = [] }: { evidence: EvidenceRead; check: Check; repos?: string[] }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<"repo" | "sha">("repo");
  const [repo, setRepo] = useState(repos[0] ?? "");
  const [other, setOther] = useState(repos.length === 0);
  const [ref, setRef] = useState("main");
  const [sha, setSha] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<HandoverResult | null>(null);

  const repoError = mode === "repo" && repo && !REPO.test(repo.trim()) ? "Give it as owner/name, e.g. harbor/website" : undefined;
  const shaError = mode === "sha" && sha && !SHA.test(sha.trim()) ? "A full 40-character commit SHA" : undefined;
  const ready = mode === "repo" ? REPO.test(repo.trim()) && !!ref.trim() : SHA.test(sha.trim());

  async function run() {
    setError(null);
    setResult(null);
    const body: HandoverCheck = mode === "repo" ? { repo: repo.trim(), ref: ref.trim() } : { delivered_sha: sha.trim() };
    try {
      setResult(await check.mutateAsync({ evidenceId: evidence.id, body }));
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  function close(o: boolean) {
    setOpen(o);
    if (!o) {
      setResult(null);
      setError(null);
    }
  }

  return (
    <>
      <Button size="sm" icon="search" onPress={() => setOpen(true)}>
        Check handover<span className="sr-only">: {evidence.title}</span>
      </Button>
      <Dialog
        isOpen={open}
        onOpenChange={close}
        title="Check the handed-over code"
        isDismissable={!check.isPending}
        footer={
          <>
            <Button variant="quiet" onPress={() => close(false)} isDisabled={check.isPending}>{result ? "Done" : "Cancel"}</Button>
            <Button variant="primary" icon="search" onPress={run} isPending={check.isPending} isDisabled={!ready}>Check</Button>
          </>
        }
      >
        <div className={layout.stack}>
          <p style={{ margin: 0 }}>
            The verifier read <strong>{evidence.title}</strong> at commit <code title={evidence.pinned_ref}>{shortHash(evidence.pinned_ref, 10, 4)}</code>.
            Check that the code you received is that commit, or has exactly the same files.
          </p>
          <RadioGroup
            label="Where is the delivered code?"
            value={mode}
            onChange={(v) => { setMode(v as "repo" | "sha"); setResult(null); }}
            options={[
              { id: "repo", label: "In a GitHub repo", description: "Looked up on GitHub; matches the same commit or the same file tree." },
              { id: "sha", label: "I have the commit SHA", description: "Compared with the verified commit directly." },
            ]}
          />
          {mode === "repo" ? (
            <div className={layout.grid2}>
              {repos.length > 0 && (
                <Select
                  label="Repo"
                  description="Repos your GitHub App installation can read"
                  value={other ? "__other__" : repo}
                  options={[...repos.map((r) => ({ id: r, label: r })), { id: "__other__", label: "Another repo" }]}
                  onChange={(v) => {
                    const isOther = v === "__other__";
                    setOther(isOther);
                    setRepo(isOther ? "" : v);
                  }}
                />
              )}
              {other && <TextField label={repos.length ? "Other repo" : "Repo"} description="owner/name" value={repo} onChange={setRepo} error={repoError} autoComplete="off" />}
              <TextField label="Branch, tag or SHA" value={ref} onChange={setRef} autoComplete="off" />
            </div>
          ) : (
            <TextField label="Delivered commit SHA" value={sha} onChange={setSha} error={shaError} figures autoComplete="off" maxLength={40} />
          )}
          <div role="status" aria-live="polite">
            {result && (
              <Callout tone={result.matches ? "success" : "error"} title={result.matches ? "Matches what was verified" : "Doesn't match what was verified"}>
                <p>{HOW[result.how] ?? result.how}.</p>
                <p style={{ fontSize: "var(--text-sm)" }}>
                  Delivered <code>{result.delivered_sha}</code>
                  <br />
                  Verified <code>{result.pinned_ref}</code>
                </p>
                <p style={{ fontSize: "var(--text-sm)", margin: 0 }}>Recorded in the tranche history and the compliance packet.</p>
              </Callout>
            )}
            {error && <Callout tone="error" title="The check didn't run">{error}</Callout>}
          </div>
        </div>
      </Dialog>
    </>
  );
}
