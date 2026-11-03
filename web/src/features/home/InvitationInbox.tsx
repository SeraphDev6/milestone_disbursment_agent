import type { InvitationRead } from "@/api/types";
import { date } from "@/domain/format";
import { ButtonLink, layout } from "@/design/components";

const KIND = { counterparty: "Receive payments under", reviewer: "Review", member: "Join" } as const;

/**
 * Pending invitations for your email, each one a step from accepting (POST /invitations/{id}/accept).
 */
export function InvitationInbox({ invitations }: { invitations: InvitationRead[] }) {
  return (
    <div className={layout.stack}>
      <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: "var(--space-3)" }}>
        {invitations.map((i) => (
          <li key={i.id} style={{ borderLeft: "4px solid var(--stamp-review-edge)", paddingLeft: "var(--space-3)" }}>
            <strong>{KIND[i.kind]} {i.agreement_title ? `“${i.agreement_title}”` : "an organization"}</strong>
            <div style={{ color: "var(--text-secondary)", fontSize: "var(--text-sm)" }}>
              {i.kind === "counterparty" ? "As the payee" : i.kind === "reviewer" ? "As a reviewer" : `As ${i.org_role ?? "member"}`} · expires {date(i.expires_at)}
            </div>
            <ButtonLink size="sm" variant="primary" href={`/invitations/accept?${new URLSearchParams({ id: i.id, kind: i.kind, ...(i.agreement_title ? { title: i.agreement_title } : {}) })}`} style={{ marginTop: "var(--space-2)" }}>
              Review and accept<span className="sr-only">: {i.agreement_title ?? "organization invitation"}</span>
            </ButtonLink>
          </li>
        ))}
      </ul>
    </div>
  );
}
