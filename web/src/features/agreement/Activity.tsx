import { useMemo, useState } from "react";
import type { ColDef } from "ag-grid-community";
import { useAgreementAudit } from "@/api/queries";
import type { AuditEventRead } from "@/api/types";
import { useAgreementCtx, useOrgCtx } from "@/app/context";
import { useTitle } from "@/app/RouteAnnouncer";
import { useSessions } from "@/auth/session";
import { actionText, actorText } from "@/domain/actors";
import { dateTime } from "@/domain/format";
import { AGREEMENT_STAMPS, MILESTONE_STAMPS } from "@/domain/status";
import { Button, Callout, DataGrid, Dialog, exportCsv, layout, Select, Stamp } from "@/design/components";

type Row = AuditEventRead & { n: number; who: string; tranche: string };

function StatusStamp({ s }: { s: string | null | undefined }) {
  if (!s) return null;
  const spec = (MILESTONE_STAMPS as Record<string, (typeof MILESTONE_STAMPS)[keyof typeof MILESTONE_STAMPS]>)[s] ?? (AGREEMENT_STAMPS as Record<string, (typeof AGREEMENT_STAMPS)[keyof typeof AGREEMENT_STAMPS]>)[s];
  return spec ? <Stamp spec={spec} flat explain={false} /> : <code>{s}</code>;
}

/** The append-only event log for the agreement, filterable, with each event's full detail on demand. */
export function Activity() {
  const { agreement: a } = useAgreementCtx();
  const org = useOrgCtx();
  useTitle("Activity", a.title);
  const q = useAgreementAudit(a.id, org.orgId);
  const { sessions } = useSessions();
  const [tranche, setTranche] = useState<string>("all");
  const [open, setOpen] = useState<Row | null>(null);
  const names = useMemo(
    () => ({ orgs: Object.fromEntries(a.parties.map((p) => [p.org_id, p.display_name])), users: Object.fromEntries(sessions.map((s) => [s.user.id, s.user.email])) }),
    [a.parties, sessions],
  );
  const titles = useMemo(() => Object.fromEntries(a.milestones.map((m, i) => [m.id, `№${i + 1} ${m.title}`])), [a.milestones]);
  const rows: Row[] = useMemo(
    () =>
      (q.data ?? [])
        .map((e, n) => ({ ...e, n, who: actorText(e.actor, names), tranche: e.milestone_id ? (titles[e.milestone_id] ?? "") : "Agreement" }))
        .filter((r) => tranche === "all" || (tranche === "agreement" ? !r.milestone_id : r.milestone_id === tranche)),
    [q.data, names, titles, tranche],
  );
  const cols = useMemo<ColDef<Row>[]>(
    () => [
      { headerName: "When", field: "at", width: 180, valueFormatter: (p) => dateTime(p.value), sort: "desc" },
      { headerName: "What", field: "action", flex: 1.2, minWidth: 180, valueFormatter: (p) => actionText(p.value) },
      { headerName: "Tranche", field: "tranche", flex: 1, minWidth: 160 },
      { headerName: "From", field: "from_status", width: 140, cellRenderer: (p: { data: Row }) => <StatusStamp s={p.data.from_status} /> },
      { headerName: "To", field: "to_status", width: 140, cellRenderer: (p: { data: Row }) => <StatusStamp s={p.data.to_status} /> },
      { headerName: "Who", field: "who", flex: 1.2, minWidth: 200 },
      {
        headerName: "",
        width: 110,
        sortable: false,
        cellRenderer: (p: { data: Row }) => (Object.keys(p.data.detail ?? {}).length ? <Button size="sm" variant="quiet" onPress={() => setOpen(p.data)}>Detail<span className="sr-only"> of {actionText(p.data.action)}</span></Button> : null),
      },
    ],
    [],
  );
  if (q.isError) return <Callout tone="error" title="Couldn't load the activity log" live>{q.error.message}</Callout>;
  return (
    <div className={layout.stack}>
      <div className={layout.row} style={{ alignItems: "flex-end", justifyContent: "space-between" }}>
        <Select
          label="Show"
          value={tranche}
          onChange={setTranche}
          options={[{ id: "all", label: "Everything" }, { id: "agreement", label: "Agreement-level events" }, ...a.milestones.map((m, i) => ({ id: m.id, label: `№${i + 1} ${m.title}` }))]}
        />
        <Button size="sm" icon="download" isDisabled={!rows.length} onPress={() => exportCsv(`audit-${a.id}.csv`, ["at", "action", "milestone_id", "from", "to", "actor", "detail"], rows.map((r) => [r.at, r.action, r.milestone_id, r.from_status, r.to_status, r.actor, JSON.stringify(r.detail)]))}>Export CSV</Button>
      </div>
      <DataGrid<Row> caption="Event log" summary={`${rows.length} events, newest first. Every change is recorded and none can be edited.`} rows={rows} columns={cols} rowId={(r) => String(r.n)} loading={q.isPending} emptyText="No events." />
      <Dialog isOpen={!!open} onOpenChange={(o) => !o && setOpen(null)} title={open ? actionText(open.action) : ""} wide>
        {open && (
          <>
            <p style={{ margin: 0, color: "var(--text-secondary)" }}>{dateTime(open.at)} · {open.who}</p>
            <pre style={{ whiteSpace: "pre-wrap", background: "var(--surface-sunken)", padding: "var(--space-3)", margin: 0, fontSize: "var(--text-sm)" }}>{JSON.stringify(open.detail, null, 2)}</pre>
          </>
        )}
      </Dialog>
    </div>
  );
}
