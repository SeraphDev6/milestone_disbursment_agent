/**
 * Audit actor strings, made readable. Formats: "user:<id>@org:<id>", "apikey:<id>@org:<id>", "system",
 * "system:verifier", "model", "webhook:paypal", "github:<login>", "payer:paypal_return".
 */
export type ActorNames = { users?: Record<string, string>; orgs?: Record<string, string> };

export type Actor = { kind: "person" | "key" | "system" | "github" | "paypal" | "verifier"; label: string; org?: string };

export function parseActor(actor: string, names: ActorNames = {}): Actor {
  const [who, org] = actor.split("@org:");
  const orgName = org ? (names.orgs?.[org] ?? "an organization") : undefined;
  if (!who) return { kind: "system", label: actor };
  if (who.startsWith("user:")) {
    const id = who.slice(5);
    return { kind: "person", label: names.users?.[id] ?? "A member", org: orgName };
  }
  if (who.startsWith("apikey:")) return { kind: "key", label: "API key", org: orgName };
  if (who.startsWith("github:")) return { kind: "github", label: `GitHub · ${who.slice(7)}` };
  if (who.startsWith("webhook:paypal") || who.includes("paypal")) return { kind: "paypal", label: "PayPal" };
  if (who === "model" || who === "system:verifier") return { kind: "verifier", label: "Verifier" };
  if (who.startsWith("party:")) return { kind: "person", label: "A party", org: orgName };
  return { kind: "system", label: who === "system" ? "System" : who.replace(/^system:/, "System · ") };
}

export function actorText(actor: string, names?: ActorNames): string {
  const a = parseActor(actor, names);
  return a.org ? `${a.label}, ${a.org}` : a.label;
}

/** "milestones_replaced" -> "Milestones replaced". */
export function actionText(action: string): string {
  const s = action.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}
