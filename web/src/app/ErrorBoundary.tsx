import { isRouteErrorResponse, useRouteError } from "react-router";
import { ButtonLink, Callout, PageHeader } from "@/design/components";
import { errorMessage, isApiError } from "@/api/errors";
import { useTitle } from "./RouteAnnouncer";

export function RouteError() {
  const err = useRouteError();
  const notFound = (isRouteErrorResponse(err) && err.status === 404) || (isApiError(err) && err.status === 404);
  useTitle(notFound ? "Not found" : "Something went wrong");
  return (
    <main id="main" style={{ padding: "var(--space-6) var(--gutter)", maxWidth: "var(--page-max)", margin: "0 auto" }}>
      <PageHeader title={notFound ? "We couldn't find that page" : "Something went wrong"} lede={notFound ? "It may have moved, or you may not have access to it from this organization." : errorMessage(err)} />
      {!notFound && (
        <Callout tone="error" title="The page failed to load">
          <p>Try again. If it keeps happening, the details below help whoever runs this server.</p>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: "var(--text-sm)" }}>{String(err instanceof Error ? err.stack ?? err.message : JSON.stringify(err))}</pre>
        </Callout>
      )}
      <p style={{ marginTop: "var(--space-5)" }}>
        <ButtonLink href="/" variant="primary">Go to your home page</ButtonLink>
      </p>
    </main>
  );
}
