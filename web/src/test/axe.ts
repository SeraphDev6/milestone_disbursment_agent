import axe from "axe-core";

/** Run axe on a rendered container. Colour contrast needs real layout, so it's covered by e2e instead. */
export async function axeViolations(container: Element): Promise<string[]> {
  const r = await axe.run(container, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } } });
  return r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`);
}
