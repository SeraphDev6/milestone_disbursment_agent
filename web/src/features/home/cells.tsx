/** Cell renderers shared by the org grids. Links are real anchors so they're reachable and announced. */
import { Link } from "react-router";
import type { MilestoneStatus, AgreementStatus } from "@/api/types";
import { AGREEMENT_STAMPS, MILESTONE_STAMPS } from "@/domain/status";
import { Amount, Stamp } from "@/design/components";

export function LinkCell({ to, children }: { to: string; children: React.ReactNode }) {
  return <Link to={to} tabIndex={-1}>{children}</Link>;
}

export function AmountCell({ value, currency }: { value: string | number; currency: string }) {
  return <span style={{ display: "block", textAlign: "right" }}><Amount value={value} currency={currency} showCurrency={false} /></span>;
}

export function MilestoneStampCell({ status }: { status: MilestoneStatus }) {
  return <Stamp spec={MILESTONE_STAMPS[status]} flat />;
}

export function AgreementStampCell({ status }: { status: AgreementStatus }) {
  return <Stamp spec={AGREEMENT_STAMPS[status]} flat />;
}
