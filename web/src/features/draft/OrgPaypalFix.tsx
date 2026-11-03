import { useState } from "react";
import { useUpdateOrg } from "@/api/queries";
import type { OrganizationRead, OrgRole } from "@/api/types";
import { errorMessage } from "@/api/errors";
import { atLeast } from "@/domain/permissions";
import { Button, Callout, TextField, layout, toast } from "@/design/components";

/** The org has no PayPal email yet: say why it matters and let an admin set it right here. */
export function OrgPaypalFix({ org, role }: { org: OrganizationRead; role: OrgRole }) {
  const update = useUpdateOrg(org.id);
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const admin = atLeast(role, "admin");
  return (
    <Callout tone="warning" title={`${org.name} has no PayPal account yet`}>
      <p>Agreements need one: it's where you fund tranches from as a payer, and get paid into as a payee.</p>
      {admin ? (
        <form
          className={layout.row}
          style={{ alignItems: "flex-end" }}
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              await update.mutateAsync({ paypal_email: email.trim() });
              toast("PayPal account saved");
            } catch (err) {
              setError(errorMessage(err));
            }
          }}
        >
          <TextField label="PayPal account email" type="email" value={email} onChange={setEmail} error={error} />
          <Button type="submit" variant="primary" isPending={update.isPending}>Save</Button>
        </form>
      ) : (
        <p>Ask an admin of {org.name} to add it in the organization settings.</p>
      )}
    </Callout>
  );
}
