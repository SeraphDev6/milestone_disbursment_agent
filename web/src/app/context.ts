import { createContext, useContext } from "react";
import type { AgreementRead, MembershipRead, Me, OrgRole, PartyRead, Role } from "@/api/types";
import type { Seat } from "@/domain/permissions";

export type OrgCtx = { orgId: string; orgName: string; role: OrgRole; me: Me; membership: MembershipRead };
export const OrgContext = createContext<OrgCtx | null>(null);
export function useOrgCtx(): OrgCtx {
  const v = useContext(OrgContext);
  if (!v) throw new Error("useOrgCtx outside an org route");
  return v;
}

export type AgreementCtx = { agreement: AgreementRead; seat: Seat; party: PartyRead; side: Role; base: string };
export const AgreementContext = createContext<AgreementCtx | null>(null);
export function useAgreementCtx(): AgreementCtx {
  const v = useContext(AgreementContext);
  if (!v) throw new Error("useAgreementCtx outside an agreement route");
  return v;
}

export const paths = {
  org: (orgId: string) => `/o/${orgId}`,
  portfolio: (orgId: string) => `/o/${orgId}/portfolio`,
  newAgreement: (orgId: string) => `/o/${orgId}/new`,
  settings: (orgId: string) => `/o/${orgId}/settings`,
  agreement: (orgId: string, aid: string) => `/o/${orgId}/a/${aid}`,
  tranche: (orgId: string, aid: string, mid: string) => `/o/${orgId}/a/${aid}/m/${mid}`,
};
