import type { ReactNode } from "react";
import { amount, money } from "@/domain/format";
import s from "./Figures.module.css";

/** Money, ledger-style: tabular figures, currency code in small caps, full value for screen readers. */
export function Amount({ value, currency = "USD", showCurrency = true }: { value: string | number | null | undefined; currency?: string; showCurrency?: boolean }) {
  return (
    <span className={s.amount}>
      <span aria-hidden="true">
        {showCurrency && <span className={s.currency}>{currency}</span>}
        {amount(value, currency)}
      </span>
      <span className="sr-only">{money(value, currency)}</span>
    </span>
  );
}

export type TotalLine = { label: ReactNode; value: string | number; hint?: string };

/** A short column of figures closed by a total with the accounting double rule. */
export function Totals({ lines, total, currency, label }: { lines: TotalLine[]; total: TotalLine; currency: string; label: string }) {
  return (
    <dl className={s.totals} aria-label={label}>
      {lines.map((l, i) => (
        <div className={s.row} key={i} title={l.hint}>
          <dt>{l.label}</dt>
          <dd><Amount value={l.value} currency={currency} showCurrency={false} /></dd>
        </div>
      ))}
      <div className={`${s.row} ${s.total}`}>
        <dt>{total.label}</dt>
        <dd><Amount value={total.value} currency={currency} /></dd>
      </div>
    </dl>
  );
}
