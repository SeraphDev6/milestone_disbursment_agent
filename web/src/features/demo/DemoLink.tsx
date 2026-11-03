import { isMock, useHealth } from "@/api/queries";
import { ButtonLink, Callout } from "@/design/components";

/** In mock mode only: point to the demo page instead of embedding demo controls in the product. */
export function DemoLink() {
  const health = useHealth();
  if (!isMock(health.data)) return null;
  return (
    <Callout tone="note" title="Trying the demo?" actions={<ButtonLink variant="primary" href="/demo" iconEnd="arrow">Open the demo</ButtonLink>}>
      <p>The demo page loads a complete grant with a funder, a grantee and a reviewer already signed in, plus sample evidence and shortcuts.</p>
    </Callout>
  );
}
