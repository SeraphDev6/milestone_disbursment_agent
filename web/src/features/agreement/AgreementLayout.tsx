import { Outlet } from "react-router";
import { useAgreementCtx, useOrgCtx, paths } from "@/app/context";
import { money, MODE_LABEL, ROLE_LABEL } from "@/domain/format";
import { AGREEMENT_STAMPS } from "@/domain/status";
import { can } from "@/domain/permissions";
import { Crumbs, layout, PageHeader, RouteTabs, Seal, Stamp } from "@/design/components";

export function AgreementLayout() {
  const { agreement: a, seat, base } = useAgreementCtx();
  const org = useOrgCtx();
  const showPacket = can(seat, "compliance_packet").ok;
  const errors = a.lint.issues?.filter((i) => i.level === "error").length ?? 0;
  return (
    <div className={layout.stack} style={{ gap: "var(--space-5)" }}>
      <PageHeader
        crumbs={<Crumbs items={[{ label: org.orgName, href: paths.org(org.orgId) }, { label: a.title }]} />}
        eyebrow={`${org.orgName} · ${ROLE_LABEL[seat.role]} · you're ${seat.orgRole}`}
        title={a.title}
        stamp={<Stamp spec={AGREEMENT_STAMPS[a.status]} size="lg" context="Agreement status" />}
        meta={
          <>
            <span className="figures">{money(a.total_amount, a.currency)}</span>
            <span>{MODE_LABEL[a.settlement_mode]}</span>
            {a.settlement_mode === "escrow_release" && <span>{a.dispute_window_days ? `${a.dispute_window_days}-day dispute window` : "No dispute window"}</span>}
            <span>{a.milestones.length} tranche{a.milestones.length === 1 ? "" : "s"}</span>
            <Seal hash={a.criteria_hash} locked={a.locked} label={a.locked ? "Locked criteria" : "Current criteria"} />
          </>
        }
      />
      <RouteTabs
        label="Agreement sections"
        links={[
          { to: base, label: "Overview", end: true },
          { to: `${base}/criteria`, label: "Criteria", badge: errors > 0 && !a.locked ? <span className="sr-only"> ({errors} errors)</span> : undefined },
          { to: `${base}/parties`, label: "Parties" },
          { to: `${base}/ledger`, label: "Ledger" },
          { to: `${base}/activity`, label: "Activity" },
          { to: `${base}/integrations`, label: "Integrations" },
          ...(showPacket ? [{ to: `${base}/packet`, label: "Compliance packet" }] : []),
        ]}
      />
      <Outlet />
    </div>
  );
}
