import { useState } from "react";
import { usePayPalAccount, usePayPalActions } from "@/api/queries";
import { errorMessage } from "@/api/errors";
import { useOrgCtx } from "@/app/context";
import { dateTime } from "@/domain/format";
import { atLeast } from "@/domain/permissions";
import { Busy, Button, Callout, Facts, Glyph, layout, PermissionNote, Sheet, Stamp, toast } from "@/design/components";

/**
 * Connecting the org's PayPal account through PayPal's partner onboarding, so tranches are paid straight
 * into it. Without it, the org is paid through the platform with PayPal Payouts to its PayPal email.
 */
export function PayPalAccount() {
  const org = useOrgCtx();
  const [waiting, setWaiting] = useState(false);
  const q = usePayPalAccount(org.orgId, waiting);
  const acts = usePayPalActions(org.orgId);
  const admin = atLeast(org.role, "admin");
  const started = !!q.data?.started;
  const acc = started ? q.data : null;

  async function connect() {
    try {
      const r = await acts.connect.mutateAsync();
      window.open(r.onboarding_url, "_blank", "noopener");
      setWaiting(true);
      toast("PayPal opened in a new tab", { body: "Finish connecting there. This page checks the status on its own." });
    } catch (e) {
      toast("Couldn't start connecting PayPal", { body: errorMessage(e), tone: "error" });
    }
  }
  async function refresh() {
    try {
      const r = await acts.refresh.mutateAsync();
      toast(r.ready ? "PayPal is connected" : "PayPal isn't ready yet", { body: r.ready ? undefined : r.issues.join("; "), tone: r.ready ? "success" : "info" });
    } catch (e) {
      toast("Couldn't check PayPal", { body: errorMessage(e), tone: "error" });
    }
  }

  return (
    <Sheet title="PayPal account" id="paypal">
      <div className={layout.stack}>
        <p>
          When {org.orgName} is the payee, a connected PayPal account is paid directly: each hold and capture is in your name and
          the platform never holds the money. Not connected, you're still paid, through the platform with a PayPal Payout to your
          PayPal email, which is slower and costs two PayPal fees instead of one.
        </p>
        <div role="status" aria-live="polite" className="sr-only">{waiting && acc?.ready ? "PayPal is connected." : ""}</div>
        {q.isPending ? (
          <Busy label="Checking PayPal" />
        ) : q.isError ? (
          <Callout tone="error" live>{q.error.message}</Callout>
        ) : !acc ? (
          <Callout tone="note" title="Not connected">You'll be paid through the platform until you connect.</Callout>
        ) : (
          <>
            <div className={layout.row}>
              <Stamp
                size="lg"
                spec={acc.ready ? { label: "Connected", tone: "settled", glyph: "check", meaning: "Tranches are paid straight into this account." } : { label: "Not ready", tone: "review", glyph: "pause", meaning: "PayPal still needs something before it can be paid directly." }}
              />
              {acc.checked_at && <span style={{ color: "var(--text-secondary)", fontSize: "var(--text-sm)" }}>Checked {dateTime(acc.checked_at)}</span>}
            </div>
            <Facts
              label="PayPal account status"
              items={[
                ["Merchant ID", acc.merchant_id ? <code key="m">{acc.merchant_id}</code> : "Not yet"],
                ["Can receive payments", <Check key="r" ok={acc.payments_receivable} />],
                ["Email confirmed", <Check key="e" ok={acc.primary_email_confirmed} />],
                ["Permissions granted", <Check key="p" ok={acc.permissions_granted} />],
              ]}
            />
            {!acc.ready && acc.issues.length > 0 && (
              <Callout tone="warning" title="What PayPal still needs">
                <ul style={{ margin: 0 }}>{acc.issues.map((i) => <li key={i}>{i}</li>)}</ul>
              </Callout>
            )}
          </>
        )}
        {admin ? (
          <div className={layout.row}>
            {(!acc || !acc.ready) && <Button variant="primary" icon="external" onPress={connect} isPending={acts.connect.isPending}>{acc ? "Continue connecting PayPal" : "Connect PayPal"}</Button>}
            {(acc || waiting) && <Button icon="refresh" onPress={refresh} isPending={acts.refresh.isPending}>Check status</Button>}
          </div>
        ) : (
          <PermissionNote>Connecting PayPal needs admin or above in {org.orgName}; you're a {org.role}.</PermissionNote>
        )}
      </div>
    </Sheet>
  );
}

function Check({ ok }: { ok: boolean }) {
  return (
    <span style={{ display: "inline-flex", gap: "var(--space-1)", alignItems: "center", color: ok ? "var(--text-success)" : "var(--text-secondary)" }}>
      <Glyph name={ok ? "check" : "ring"} size={14} />
      {ok ? "Yes" : "Not yet"}
    </span>
  );
}
