import { Navigate, Outlet, useLocation, useParams } from "react-router";
import { useAgreement, useMe } from "@/api/queries";
import { isApiError } from "@/api/errors";
import { lastOrg, useActiveSession } from "@/auth/session";
import { Busy, ButtonLink, Callout, PageHeader } from "@/design/components";
import { AgreementContext, OrgContext, paths, useOrgCtx } from "./context";
import { useTitle } from "./RouteAnnouncer";

/** Signed-in pages. No session: go to sign-in and come back afterwards. */
export function RequireSession() {
  const session = useActiveSession();
  const loc = useLocation();
  if (!session) return <Navigate to={`/sign-in?returnTo=${encodeURIComponent(loc.pathname + loc.search)}`} replace />;
  return <Outlet />;
}

/** "/" goes to the org you last acted for, or onboarding when you have none. */
export function HomeRedirect() {
  const session = useActiveSession();
  const me = useMe();
  if (me.isPending) return <Busy label="Opening your ledger" />;
  if (me.isError) return <Callout tone="error" title="Couldn't load your account" live>{String(me.error.message)}</Callout>;
  const ms = me.data.memberships;
  if (!ms.length) return <Navigate to="/welcome" replace />;
  const remembered = session && lastOrg.get(session.user.id);
  const target = ms.find((m) => m.org_id === remembered) ?? ms[0]!;
  return <Navigate to={paths.org(target.org_id)} replace />;
}

/** /o/:orgId — you must belong to the org; its name and your role are provided to the pages below. */
export function OrgRoute() {
  const { orgId = "" } = useParams();
  const session = useActiveSession();
  const me = useMe();
  if (me.isPending) return <Busy label="Loading" />;
  if (me.isError) return <Callout tone="error" title="Couldn't load your account" live>{me.error.message}</Callout>;
  const membership = me.data.memberships.find((m) => m.org_id === orgId);
  if (!membership) return <NotMember />;
  if (session) lastOrg.set(session.user.id, orgId);
  return (
    <OrgContext.Provider value={{ orgId, orgName: membership.org_name, role: membership.role, me: me.data, membership }}>
      <Outlet />
    </OrgContext.Provider>
  );
}

function NotMember() {
  useTitle("No access");
  return (
    <>
      <PageHeader title="You're not a member of this organization" lede="Ask one of its admins to invite you, or switch to an organization you belong to." />
      <ButtonLink href="/" variant="primary">Go to your home page</ButtonLink>
    </>
  );
}

/** /o/:orgId/a/:agreementId — loads the agreement as this org and works out your seat on it. */
export function AgreementRoute() {
  const { agreementId = "" } = useParams();
  const org = useOrgCtx();
  const q = useAgreement(agreementId, org.orgId);
  if (q.isPending) return <Busy label="Loading agreement" lines={6} />;
  if (q.isError) {
    const notFound = isApiError(q.error) && q.error.status === 404;
    return (
      <>
        <PageHeader title={notFound ? "Agreement not found" : "Couldn't load this agreement"} lede={notFound ? `${org.orgName} isn't a party to this agreement, or it doesn't exist.` : q.error.message} />
        <ButtonLink href={paths.org(org.orgId)} variant="primary">Back to {org.orgName}</ButtonLink>
      </>
    );
  }
  const a = q.data;
  const party = a.parties.find((p) => p.org_id === org.orgId);
  if (!party) return <Callout tone="error" title="Not a party">{org.orgName} has no seat on this agreement.</Callout>;
  return (
    <AgreementContext.Provider
      value={{ agreement: a, party, side: party.role, seat: { role: party.role, orgRole: org.role, orgName: org.orgName }, base: paths.agreement(org.orgId, a.id) }}
    >
      <Outlet />
    </AgreementContext.Provider>
  );
}
