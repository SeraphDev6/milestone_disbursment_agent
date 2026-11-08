import { useMemo } from "react";
import type { ColDef } from "ag-grid-community";
import { useLedger, type DisbursementRow, type LedgerEntry, type SettlementRow } from "@/api/queries";
import { useAgreementCtx, useOrgCtx } from "@/app/context";
import { useTitle } from "@/app/RouteAnnouncer";
import { dateTime, toNumber } from "@/domain/format";
import { SETTLEMENT_STAMPS } from "@/domain/status";
import { Busy, Button, Callout, DataGrid, exportCsv, layout, Stamp } from "@/design/components";
import { AmountCell } from "@/features/home/cells";

const KIND: Record<string, { label: string; sign: number; effect: string }> = {
  authorized: { label: "Hold placed", sign: 1, effect: "held" },
  captured: { label: "Captured", sign: 1, effect: "in_dispute_window" },
  disbursed: { label: "Paid to payee", sign: 1, effect: "released" },
  refunded: { label: "Refunded to payer", sign: 1, effect: "refunded" },
  voided: { label: "Hold cancelled", sign: -1, effect: "held" },
  expired: { label: "Hold expired", sign: -1, effect: "held" },
  invoiced: { label: "Invoice sent", sign: 1, effect: "invoiced" },
  paid: { label: "Invoice paid", sign: 1, effect: "released" },
  collected: { label: "Invoice collected", sign: 1, effect: "in_dispute_window" },
  invoice_cancelled: { label: "Invoice cancelled", sign: -1, effect: "invoiced" },
};

type Row = LedgerEntry & { tranche: string; released_to_date: number };

/** The money record: every PayPal movement, with a running total released, plus each settlement attempt. */
export function Ledger() {
  const { agreement: a } = useAgreementCtx();
  const org = useOrgCtx();
  useTitle("Ledger", a.title);
  const q = useLedger(a.id, org.orgId);
  const titles = useMemo(() => Object.fromEntries(a.milestones.map((m, i) => [m.id, `№${i + 1} ${m.title}`])), [a.milestones]);

  const rows: Row[] = useMemo(
    () =>
      [...(q.data?.entries ?? [])]
        .sort((x, y) => x.at.localeCompare(y.at))
        .reduce<Row[]>((acc, e) => {
          const before = acc.at(-1)?.released_to_date ?? 0;
          const released = before + (e.kind === "disbursed" || e.kind === "paid" ? toNumber(e.amount) : 0);
          return [...acc, { ...e, tranche: e.milestone_id ? (titles[e.milestone_id] ?? "") : "", released_to_date: released }];
        }, []),
    [q.data, titles],
  );

  const cols = useMemo<ColDef<Row>[]>(
    () => [
      { headerName: "When", field: "at", width: 180, valueFormatter: (p) => dateTime(p.value), sort: "asc" },
      { headerName: "Tranche", field: "tranche", flex: 1.4, minWidth: 180 },
      { headerName: "Movement", field: "kind", width: 170, valueFormatter: (p) => KIND[p.value]?.label ?? p.value },
      { headerName: "Amount", field: "amount", width: 140, type: "rightAligned", cellRenderer: (p: { data: Row }) => <AmountCell value={p.data.amount} currency={a.currency} /> },
      { headerName: "Paid out to date", field: "released_to_date", width: 160, type: "rightAligned", cellRenderer: (p: { data: Row }) => <AmountCell value={p.data.released_to_date} currency={a.currency} /> },
      { headerName: "PayPal reference", field: "paypal_ref", flex: 1, minWidth: 180, cellClass: "figures" },
    ],
    [a.currency],
  );

  const sCols = useMemo<ColDef<SettlementRow>[]>(
    () => [
      { headerName: "Tranche", valueGetter: (p) => (p.data ? titles[p.data.milestone_id] : ""), flex: 1.4, minWidth: 180 },
      { headerName: "Attempt", field: "attempt", width: 100, type: "rightAligned" },
      { headerName: "By", field: "mode", width: 130, valueFormatter: (p) => (p.value === "escrow_release" ? "Capture" : "Invoice") },
      { headerName: "Amount", field: "amount", width: 130, type: "rightAligned", cellRenderer: (p: { data: SettlementRow }) => <AmountCell value={p.data.amount} currency={a.currency} /> },
      ...(q.data?.settlements.some((x) => x.funder) ? [{ headerName: "From", field: "funder", width: 170 } as ColDef<SettlementRow>] : []),
      { headerName: "Status", field: "status", width: 140, cellRenderer: (p: { data: SettlementRow }) => <Stamp spec={SETTLEMENT_STAMPS[p.data.status]} flat /> },
      { headerName: "Idempotency key", field: "idempotency_key", flex: 1, minWidth: 200, cellClass: "figures" },
      { headerName: "PayPal reference", field: "paypal_ref", flex: 1, minWidth: 160, cellClass: "figures" },
      { headerName: "Error", field: "error", flex: 1, minWidth: 160 },
    ],
    [titles, a.currency, q.data],
  );

  const dCols = useMemo<ColDef<DisbursementRow>[]>(
    () => [
      { headerName: "Tranche", valueGetter: (p) => (p.data ? titles[p.data.milestone_id] : ""), flex: 1.4, minWidth: 180 },
      { headerName: "What", field: "kind", width: 190, valueFormatter: (p) => ({ referenced_payout: "Payout (direct)", payout: "Payout (platform)", refund: "Refund to payer" })[p.value as string] ?? p.value },
      { headerName: "Attempt", field: "attempt", width: 100, type: "rightAligned" },
      { headerName: "Status", field: "status", width: 140, cellRenderer: (p: { data: DisbursementRow }) => <Stamp spec={SETTLEMENT_STAMPS[p.data.status]} flat /> },
      { headerName: "Idempotency key", field: "idempotency_key", flex: 1, minWidth: 200, cellClass: "figures" },
      { headerName: "PayPal reference", field: "paypal_ref", flex: 1, minWidth: 160, cellClass: "figures" },
      { headerName: "Error", field: "error", flex: 1, minWidth: 160 },
    ],
    [titles],
  );

  if (q.isPending) return <Busy label="Loading ledger" lines={5} />;
  if (q.isError) return <Callout tone="error" title="Couldn't load the ledger" live>{q.error.message}</Callout>;
  return (
    <div className={layout.stackLg}>
      <DataGrid<Row>
        caption="Money movements"
        summary={`${rows.length} PayPal movements. ${a.funding.released} ${a.currency} paid to the payee so far.`}
        rows={rows}
        columns={cols}
        rowId={(r) => r.id}
        emptyText="No money has moved yet."
        actions={
          <Button size="sm" icon="download" isDisabled={!rows.length} onPress={() => exportCsv(`ledger-${a.id}.csv`, ["at", "tranche", "kind", "amount", "currency", "released_to_date", "paypal_ref"], rows.map((r) => [r.at, r.tranche, r.kind, r.amount, a.currency, r.released_to_date.toFixed(2), r.paypal_ref]))}>
            Export CSV
          </Button>
        }
      />
      <DataGrid<SettlementRow>
        caption="Captures and invoices"
        summary={`${q.data.settlements.length} capture or invoice attempts. Each attempt has its own idempotency key, so a retry never pays twice.`}
        rows={q.data.settlements}
        columns={sCols}
        rowId={(r) => r.id}
        emptyText="No tranche has been released or invoiced yet."
      />
      <DataGrid<DisbursementRow>
        caption="Payouts and refunds"
        summary={`${q.data.disbursements.length} payouts or refunds after capture.`}
        rows={q.data.disbursements}
        columns={dCols}
        rowId={(r) => r.id}
        emptyText="Nothing has been paid out or refunded after a capture yet."
      />
    </div>
  );
}
