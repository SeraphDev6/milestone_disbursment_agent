import { shortHash } from "@/domain/format";
import { CopyButton } from "./CopyButton";
import { Glyph } from "./Glyph";
import s from "./Seal.module.css";

/** The criteria hash, as a seal: short form on screen, full value to assistive tech and the clipboard. */
export function Seal({ hash, locked, label = "Criteria seal" }: { hash: string; locked?: boolean; label?: string }) {
  return (
    <span className={s.seal}>
      <span className={s.label}>{label}</span>
      <span className={`${s.mark} ${locked ? s.locked : ""}`}>
        <Glyph name={locked ? "stamp" : "signature"} size={14} />
        <span aria-hidden="true">{shortHash(hash, 6, 4)}</span>
        <span className="sr-only">{locked ? "locked, " : ""}SHA-256 {hash}</span>
      </span>
      <CopyButton value={hash} label="Copy full criteria hash" />
    </span>
  );
}
