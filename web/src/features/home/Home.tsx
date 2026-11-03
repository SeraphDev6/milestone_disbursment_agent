import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";
import type { ColDef } from "ag-grid-community";
import { useOrgAgreements, useReviewQueue } from "@/api/queries";
import type { AgreementStatus, AgreementSummary, ReviewItem } from "@/api/types";
import { paths, useOrgCtx } from "@/app/context";
import { useTitle } from "@/app/RouteAnnouncer";
import { money, percent, ROLE_LABEL, SIDE_NOUN } from "@/domain/format";
import { MILESTONE_STAMPS, NEEDS_EVIDENCE } from "@/domain/status";
import { ButtonLink, Callout, DataGrid, EmptyState, Glyph, layout, PageHeader, Sheet, Stamp, Tabs } from "@/design/components";
import { AgreementStampCell, AmountCell, LinkCell } from "./cells";
import { DemoLink } from "@/features/demo/DemoLink";
import { InvitationInbox } from "./InvitationInbox";
import s from "./Home.module.css";

type Side = "all" | "payer" | "payee" | "reviewer";

export function Home() {
  const org = useOrgCtx();
  useTitle(org.orgName);
  const queue = useReviewQueue(org.orgId);
  const all = useOrgAgreements(org.orgId);
  const invitations = org.me.invitations;
  const payeeWork = (all.data ?? []).filter((a) => a.side === "payee" && a.needs_attention > 0 && a.status === "active");

  return (
    <div className={layout.stackLg}>
      <PageHeader
        eyebrow={`${org.orgName} · ${org.role}`}
        title="Ledger"
        lede="What's waiting on your organization, and every agreement it's party to."
        actions={<ButtonLink href={paths.newAgreement(org.orgId)} variant="primary" icon="plus">New agreement</ButtonLink>}
      />

      <WaitingOnYou queue={queue.data} payeeWork={payeeWork} loading={queue.isPending || all.isPending} orgId={org.orgId} />

      {invitations.length > 0 && (
        <Sheet title={`Invitations for ${org.me.user.email}`}>
          <InvitationInbox invitations={invitations} />
        </Sheet>
      )}

      <AgreementsBySide orgId={org.orgId} counts={all.data} />

      {all.data?.length === 0 && <DemoLink />}
    </div>
  );
}

function WaitingOnYou({ queue, payeeWork, loading, orgId }: { queue?: ReviewItem[]; payeeWork: AgreementSummary[]; loading: boolean; orgId: string }) {
  const items = queue ?? [];
  const count = items.length + payeeWork.length;
  return (
    <Sheet title={<>Waiting on you {count > 0 && <span className={s.count}>{count}</span>}</>}>
      {loading ? (
        <p className={s.muted}>Checking…</p>
      ) : count === 0 ? (
        <EmptyState title="Nothing is waiting on you.">Verdicts that need a person, disputes and tranches that need evidence show up here.</EmptyState>
      ) : (
        <ol className={s.queue}>
          {items.map((i) => (
            <li key={i.milestone_id} className={s.queueItem}>
              <Stamp spec={MILESTONE_STAMPS[i.status]} />
              <div className={s.queueBody}>
                <Link to={paths.tranche(orgId, i.agreement_id, i.milestone_id)}>{i.milestone_title}</Link>
                <span className={s.muted}>
                  {i.agreement_title} · {money(i.amount)} · as {ROLE_LABEL[i.side].toLowerCase()}
                  {i.verdict_confidence !== null && i.verdict_confidence !== undefined && ` · verifier ${i.verdict_met ? "found it met" : "found gaps"} (${percent(i.verdict_confidence)})`}
                </span>
              </div>
              <span className={s.queueAction}>{i.status === "disputed" ? "Re-review the dispute" : "Approve or reject"}</span>
            </li>
          ))}
          {payeeWork.map((a) => (
            <li key={a.id} className={s.queueItem}>
              <Stamp spec={NEEDS_EVIDENCE} />
              <div className={s.queueBody}>
                <Link to={paths.agreement(orgId, a.id)}>{a.title}</Link>
                <span className={s.muted}>{a.needs_attention} tranche{a.needs_attention === 1 ? "" : "s"} waiting for evidence or a settlement fix</span>
              </div>
              <span className={s.queueAction}>Submit evidence</span>
            </li>
          ))}
        </ol>
      )}
    </Sheet>
  );
}

function AgreementsBySide({ orgId, counts }: { orgId: string; counts?: AgreementSummary[] }) {
  const [side, setSide] = useState<Side>("all");
  const n = (s: Side) => (counts ?? []).filter((a) => s === "all" || a.side === s).length;
  const tabs = (["all", "payer", "payee", "reviewer"] as Side[]).map((sd) => ({
    id: sd,
    label: `${sd === "all" ? "All" : SIDE_NOUN[sd]} (${counts ? n(sd) : "…"})`,
    content: <AgreementGrid orgId={orgId} side={sd} />,
  }));
  return (
    <section aria-labelledby="agreements-title" className={layout.stack}>
      <h2 id="agreements-title">Agreements</h2>
      <Tabs label="Filter agreements by your side" tabs={tabs} selected={side} onSelect={(k) => setSide(k as Side)} />
    </section>
  );
}

function AgreementGrid({ orgId, side }: { orgId: string; side: Side }) {
  const q = useOrgAgreements(orgId, side === "all" ? undefined : side);
  const navigate = useNavigate();
  const rows = q.data;
  const columns = useMemo<ColDef<AgreementSummary>[]>(
    () => [
      {
        headerName: "Agreement",
        field: "title",
        flex: 2,
        minWidth: 220,
        cellRenderer: (p: { data: AgreementSummary }) => <LinkCell to={paths.agreement(orgId, p.data.id)}>{p.data.title}</LinkCell>,
      },
      { headerName: "Side", field: "side", width: 120, valueFormatter: (p) => ROLE_LABEL[p.value as keyof typeof ROLE_LABEL] },
      { headerName: "With", valueGetter: (p) => p.data?.counterparties.join(", "), flex: 1.4, minWidth: 180 },
      { headerName: "Status", field: "status", width: 150, cellRenderer: (p: { data: AgreementSummary }) => <AgreementStampCell status={p.data.status as AgreementStatus} /> },
      { headerName: "Total", field: "total_amount", width: 140, type: "rightAligned", comparator: (a, b) => Number(a) - Number(b), cellRenderer: (p: { data: AgreementSummary }) => <AmountCell value={p.data.total_amount} currency={p.data.currency} /> },
      { headerName: "Held", field: "held", width: 130, type: "rightAligned", comparator: (a, b) => Number(a) - Number(b), cellRenderer: (p: { data: AgreementSummary }) => <AmountCell value={p.data.held} currency={p.data.currency} /> },
      { headerName: "Released", field: "released", width: 130, type: "rightAligned", comparator: (a, b) => Number(a) - Number(b), cellRenderer: (p: { data: AgreementSummary }) => <AmountCell value={p.data.released} currency={p.data.currency} /> },
      { headerName: "Tranches", field: "tranches", width: 110, type: "rightAligned" },
      {
        headerName: "Needs you",
        field: "needs_attention",
        width: 120,
        sort: "desc",
        cellRenderer: (p: { value: number }) => (p.value > 0 ? <strong><Glyph name="warning" size={12} style={{ display: "inline" }} /> {p.value}</strong> : <span style={{ color: "var(--text-secondary)" }}>—</span>),
      },
    ],
    [orgId],
  );
  if (q.isError) return <Callout tone="error" title="Couldn't load agreements" live>{q.error.message}</Callout>;
  const attention = (rows ?? []).filter((r) => r.needs_attention > 0).length;
  return (
    <DataGrid<AgreementSummary>
      caption={side === "all" ? "All agreements" : `${SIDE_NOUN[side]}`}
      summary={rows ? `${rows.length} agreements, ${attention} need your attention. Press Enter on a title to open it.` : "Loading agreements"}
      rows={rows}
      columns={columns}
      rowId={(r) => `${r.id}-${r.side}`}
      loading={q.isPending}
      emptyText={side === "payer" ? "Your organization isn't paying under any agreements yet." : side === "payee" ? "No agreements pay your organization yet." : side === "reviewer" ? "You haven't been asked to review any agreements." : "No agreements yet. Create one, or accept an invitation."}
      onRowActivate={(r) => navigate(paths.agreement(orgId, r.id))}
    />
  );
}
