import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import type { ColDef } from "ag-grid-community";
import { AgCharts } from "ag-charts-react";
import { BarSeriesModule, CategoryAxisModule, LegendModule, ModuleRegistry as ChartsRegistry, NumberAxisModule, type AgCartesianChartOptions } from "ag-charts-community";
import { usePortfolio } from "@/api/queries";
import type { AgreementSummary } from "@/api/types";
import { paths, useOrgCtx } from "@/app/context";
import { useTitle } from "@/app/RouteAnnouncer";
import { amount, money, toNumber } from "@/domain/format";
import { Busy, Button, Callout, DataGrid, EmptyState, exportCsv, layout, PageHeader, Sheet, Switch, Totals } from "@/design/components";
import { useCssVars } from "@/design/useCssVars";
import { AgreementStampCell, AmountCell, LinkCell } from "@/features/home/cells";
import type { AgreementStatus } from "@/api/types";

ChartsRegistry.registerModules([BarSeriesModule, CategoryAxisModule, NumberAxisModule, LegendModule]);

type Row = AgreementSummary & { unfunded: number };

export function Portfolio() {
  const org = useOrgCtx();
  useTitle("Portfolio", org.orgName);
  const q = usePortfolio(org.orgId);
  const navigate = useNavigate();

  const rows: Row[] = useMemo(
    // Same rule as the server's funding summary: refunded money counts as unfunded again.
    () => (q.data?.agreements ?? []).map((a) => ({ ...a, unfunded: Math.max(0, toNumber(a.total_amount) - toNumber(a.held) - toNumber(a.in_dispute_window) - toNumber(a.invoiced) - toNumber(a.released)) })),
    [q.data],
  );

  const columns = useMemo<ColDef<Row>[]>(
    () => [
      { headerName: "Grant or SOW", field: "title", flex: 2, minWidth: 220, cellRenderer: (p: { data: Row }) => <LinkCell to={paths.agreement(org.orgId, p.data.id)}>{p.data.title}</LinkCell> },
      { headerName: "Payee", valueGetter: (p) => p.data?.counterparties.join(", "), flex: 1.2, minWidth: 160 },
      { headerName: "Status", field: "status", width: 150, cellRenderer: (p: { data: Row }) => <AgreementStampCell status={p.data.status as AgreementStatus} /> },
      ...(["total_amount", "released", "in_dispute_window", "held", "invoiced", "refunded", "unfunded"] as const).map(
        (f): ColDef<Row> => ({
          headerName: { total_amount: "Total", released: "Paid out", in_dispute_window: "In window", held: "Held", invoiced: "Invoiced", refunded: "Refunded", unfunded: "Unfunded" }[f],
          field: f,
          width: 135,
          type: "rightAligned",
          comparator: (a, b) => toNumber(a) - toNumber(b),
          cellRenderer: (p: { data: Row }) => <AmountCell value={p.data[f]} currency={p.data.currency} />,
        }),
      ),
      { headerName: "At risk", field: "needs_attention", width: 110, type: "rightAligned", sort: "desc" },
    ],
    [org.orgId],
  );

  if (q.isPending) return <Busy label="Loading portfolio" lines={5} />;
  if (q.isError) return <Callout tone="error" title="Couldn't load the portfolio" live>{q.error.message}</Callout>;

  const currencies = Object.entries(q.data.currency_totals);
  const atRisk = rows.reduce((n, r) => n + r.needs_attention, 0);

  return (
    <div className={layout.stackLg}>
      <PageHeader eyebrow={org.orgName} title="Portfolio" lede="Money across every agreement your organization pays: released to payees, held in PayPal, invoiced, and not yet funded." />
      {rows.length === 0 ? (
        <EmptyState title="No agreements where you're the payer yet." actions={<Button variant="primary" onPress={() => navigate(paths.newAgreement(org.orgId))}>Draft an agreement</Button>}>
          Once you fund tranches and they settle, this page shows where the money is.
        </EmptyState>
      ) : (
        <>
          <div className={layout.grid2}>
            {currencies.map(([cur, t]) => (
              <Sheet key={cur} title={`${cur} committed`} titleLevel={3} tight>
                <Totals
                  label={`${cur} totals`}
                  currency={cur}
                  lines={[
                    { label: "Paid to payees", value: t.released ?? 0, hint: "Paid out after capture, plus paid invoices" },
                    { label: "In the dispute window", value: t.in_dispute_window ?? 0, hint: "Captured; paid when each window closes" },
                    { label: "Held in PayPal", value: t.held ?? 0, hint: "Authorized on your account, not yet captured" },
                    { label: "Invoiced", value: t.invoiced ?? 0 },
                    { label: "Not yet funded", value: toNumber(t.total) - toNumber(t.released) - toNumber(t.in_dispute_window) - toNumber(t.held) - toNumber(t.invoiced) },
                  ]}
                  total={{ label: "Total committed", value: t.total ?? 0 }}
                />
              </Sheet>
            ))}
            <Sheet title="At risk" titleLevel={3} tight>
              <p style={{ fontFamily: "var(--font-display)", fontSize: "var(--text-3xl)", margin: 0 }}>{atRisk}</p>
              <p style={{ color: "var(--text-secondary)" }}>tranches waiting on review, disputed, or failed to settle.</p>
            </Sheet>
          </div>
          <MoneyChart rows={rows} />
          <DataGrid<Row>
            caption="Agreements you pay"
            summary={`${rows.length} agreements; ${atRisk} tranches at risk.`}
            rows={rows}
            columns={columns}
            rowId={(r) => r.id}
            onRowActivate={(r) => navigate(paths.agreement(org.orgId, r.id))}
            actions={
              <Button
                size="sm"
                icon="download"
                onPress={() =>
                  exportCsv(
                    `portfolio-${org.orgName.replace(/\W+/g, "-").toLowerCase()}.csv`,
                    ["agreement", "payee", "status", "currency", "total", "paid_out", "in_dispute_window", "held", "invoiced", "refunded", "unfunded", "at_risk"],
                    rows.map((r) => [r.title, r.counterparties.join("; "), r.status, r.currency, r.total_amount, r.released, r.in_dispute_window, r.held, r.invoiced, r.refunded, r.unfunded.toFixed(2), r.needs_attention]),
                  )
                }
              >
                Export CSV
              </Button>
            }
          />
        </>
      )}
    </div>
  );
}

const SERIES = [
  { key: "released", name: "Paid out", token: "chart-released" },
  { key: "window", name: "In dispute window", token: "chart-window" },
  { key: "held", name: "Held", token: "chart-held" },
  { key: "invoiced", name: "Invoiced", token: "chart-invoiced" },
] as const;
/** The remainder of each total: drawn as a recessive neutral track, not a fourth categorical hue. */
const UNFUNDED = { key: "unfunded", name: "Not yet funded" } as const;

/** Released / held / invoiced per agreement, stacked against its total. A table view is one switch away. */
function MoneyChart({ rows }: { rows: Row[] }) {
  const [asTable, setAsTable] = useState(false);
  const c = useCssVars(["chart-released", "chart-window", "chart-held", "chart-invoiced", "surface-sheet", "surface-sunken", "text-primary", "text-secondary", "rule-hairline", "border-control"] as const);
  const currency = rows[0]?.currency ?? "USD";
  const data = rows.map((r) => ({ title: r.title, released: toNumber(r.released), window: toNumber(r.in_dispute_window), held: toNumber(r.held), invoiced: toNumber(r.invoiced), unfunded: r.unfunded }));
  const totalReleased = data.reduce((n, d) => n + d.released, 0);
  const totalHeld = data.reduce((n, d) => n + d.held, 0);
  const totalWindow = data.reduce((n, d) => n + d.window, 0);
  const summary = `Across ${rows.length} agreements: ${money(totalReleased, currency)} paid out, ${money(totalWindow, currency)} in dispute windows and ${money(totalHeld, currency)} held.`;

  const options: AgCartesianChartOptions = {
    data,
    height: Math.max(180, 56 * rows.length + 90),
    background: { fill: c["surface-sheet"] },
    padding: { top: 8, right: 24, bottom: 8, left: 8 },
    series: [...SERIES.map((s) => ({
      type: "bar" as const,
      direction: "horizontal" as const,
      xKey: "title",
      yKey: s.key,
      yName: s.name,
      stacked: true,
      fill: c[s.token],
      stroke: c["surface-sheet"],
      strokeWidth: 2,
      cornerRadius: 0,
      tooltip: { renderer: ({ datum, yKey }: { datum: Record<string, number | string>; yKey: string }) => ({ heading: String(datum.title), title: SERIES.find((x) => x.key === yKey)?.name, data: [{ label: currency, value: amount(datum[yKey] as number) }] }) },
    })), {
      type: "bar" as const,
      direction: "horizontal" as const,
      xKey: "title",
      yKey: UNFUNDED.key,
      yName: UNFUNDED.name,
      stacked: true,
      fill: c["surface-sunken"],
      stroke: c["border-control"],
      strokeWidth: 1,
      lineDash: [3, 3],
      tooltip: { renderer: ({ datum }: { datum: Record<string, number | string> }) => ({ heading: String(datum.title), title: UNFUNDED.name, data: [{ label: currency, value: amount(datum.unfunded as number) }] }) },
    }],
    axes: {
      x: { type: "category", position: "left", label: { color: c["text-primary"], fontFamily: "Atkinson Hyperlegible Next", fontSize: 13 }, line: { stroke: c["border-control"] } },
      y: {
        type: "number",
        position: "bottom",
        label: { color: c["text-secondary"], fontFamily: "Atkinson Hyperlegible Mono", fontSize: 12, formatter: ({ value }: { value: number }) => (value >= 1000 ? `${value / 1000}k` : String(value)) },
        gridLine: { style: [{ stroke: c["rule-hairline"], lineDash: [2, 4] }] },
      },
    } as AgCartesianChartOptions["axes"],
    legend: { position: "top", item: { label: { color: c["text-primary"], fontFamily: "Atkinson Hyperlegible Next", fontSize: 13 } } },
  };

  return (
    <Sheet title="Where the money is" actions={<Switch isSelected={asTable} onChange={setAsTable}>Show as table</Switch>}>
      <p className="sr-only">{summary}</p>
      {asTable ? (
        <table>
          <caption className="sr-only">Paid out, in dispute window, held and invoiced per agreement, {currency}</caption>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--rule-strong)" }}><th scope="col">Agreement</th>{SERIES.map((s) => <th key={s.key} scope="col" style={{ textAlign: "right" }}>{s.name}</th>)}<th scope="col" style={{ textAlign: "right" }}>Unfunded</th></tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.title} style={{ borderBottom: "1px solid var(--rule-hairline)" }}>
                <th scope="row" style={{ fontWeight: 400, padding: "var(--space-2) 0" }}>{d.title}</th>
                {SERIES.map((s) => <td key={s.key} className="figures" style={{ textAlign: "right" }}>{amount(d[s.key])}</td>)}
                <td className="figures" style={{ textAlign: "right" }}>{amount(d.unfunded)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div inert aria-hidden="true" style={{ width: "100%", overflowX: "auto" }}>
          <AgCharts options={options} />
        </div>
      )}
      {!asTable && <p style={{ color: "var(--text-secondary)", fontSize: "var(--text-sm)", marginTop: "var(--space-2)" }}>{summary} The dashed part of each bar is not yet funded. Switch to the table for exact figures.</p>}
    </Sheet>
  );
}
