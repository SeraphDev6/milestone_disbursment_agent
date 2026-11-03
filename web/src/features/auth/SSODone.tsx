import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { api, call } from "@/api/client";
import { errorMessage } from "@/api/errors";
import { sessions } from "@/auth/session";
import { useTitle } from "@/app/RouteAnnouncer";
import { Busy, ButtonLink, Callout, layout, PageHeader } from "@/design/components";

/**
 * Where the identity provider's round trip ends: /app/auth/sso#token=…. The token is in the fragment (never
 * sent to a server or logged); it's read once and removed from the address bar immediately.
 */
export function SSODone() {
  useTitle("Signing in");
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;
    const token = new URLSearchParams(window.location.hash.slice(1)).get("token");
    window.history.replaceState(null, "", window.location.pathname);
    let prefs = { keep: true, returnTo: "/" };
    try {
      prefs = { ...prefs, ...JSON.parse(sessionStorage.getItem("mde.sso") ?? "{}") };
      sessionStorage.removeItem("mde.sso");
    } catch {
      /* defaults */
    }
    if (!token) {
      setError("Your organization's sign-in didn't return a session.");
      return;
    }
    call(api.GET("/me", { headers: { Authorization: `Bearer ${token}` } }))
      .then((me) => {
        sessions.add(token, me.user, prefs.keep);
        qc.clear();
        navigate(prefs.returnTo.startsWith("/") ? prefs.returnTo : "/", { replace: true });
      })
      .catch((e) => setError(errorMessage(e)));
  }, [navigate, qc]);

  if (error)
    return (
      <div className={layout.stack}>
        <PageHeader title="Single sign-on didn't finish" />
        <Callout tone="error" live>{error}</Callout>
        <ButtonLink href="/sign-in" variant="primary">Try again</ButtonLink>
      </div>
    );
  return <Busy label="Signing you in" />;
}
