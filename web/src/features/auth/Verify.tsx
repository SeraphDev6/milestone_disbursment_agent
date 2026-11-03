import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { verifyMagicLink } from "@/api/queries";
import { errorMessage } from "@/api/errors";
import { sessions } from "@/auth/session";
import { useTitle } from "@/app/RouteAnnouncer";
import { Busy, ButtonLink, Callout, layout, PageHeader } from "@/design/components";

/**
 * Finishes sign-in. The token is POSTed (never consumed by a GET), so email scanners that prefetch the
 * link can't use it up. The token is removed from the address bar as soon as it's read.
 */
export function Verify() {
  useTitle("Signing in");
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const ran = useRef(false);
  const token = params.get("token");
  const keep = params.get("keep") !== "0";
  const returnTo = params.get("returnTo") || "/";

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;
    window.history.replaceState(null, "", window.location.pathname);
    if (!token) {
      setError("This link is missing its sign-in token.");
      return;
    }
    verifyMagicLink(token)
      .then((r) => {
        sessions.add(r.token, r.user, keep);
        qc.clear();
        navigate(returnTo.startsWith("/") ? returnTo : "/", { replace: true });
      })
      .catch((e) => setError(errorMessage(e)));
  }, [token, keep, returnTo, navigate, qc]);

  if (error)
    return (
      <div className={layout.stack}>
        <PageHeader title="That sign-in link didn't work" lede="Links work once and expire after 15 minutes." />
        <Callout tone="error" live>{error}</Callout>
        <ButtonLink href="/sign-in" variant="primary">Send a new link</ButtonLink>
      </div>
    );
  return <Busy label="Signing you in" />;
}
