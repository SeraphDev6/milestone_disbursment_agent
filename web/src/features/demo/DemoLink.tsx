import { hasDemo, useHealth } from "@/api/queries";
import { ButtonLink, Callout } from "@/design/components";

/** When the server has the demo: point to its page instead of embedding demo controls in the product. */
export function DemoLink() {
  const health = useHealth();
  if (!hasDemo(health.data)) return null;
  return (
    <Callout tone="note" title="Trying the demo?" actions={<ButtonLink variant="primary" href="/demo" iconEnd="arrow">Open the demo</ButtonLink>}>
      <p>The demo page loads a complete grant with a funder, a grantee and a reviewer already signed in, plus sample evidence and shortcuts.</p>
    </Callout>
  );
}
