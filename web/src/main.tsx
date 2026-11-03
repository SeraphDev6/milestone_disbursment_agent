import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router";
import { isApiError } from "@/api/errors";
import "@/design/fonts";
import "@/design/tokens.css";
import "@/design/themes.css";
import "@/design/base.css";
import { router } from "@/app/router";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      // Don't retry what won't change: auth, permission and not-found answers.
      retry: (count, err) => !(isApiError(err) && [401, 403, 404, 409, 422].includes(err.status)) && count < 2,
      refetchOnWindowFocus: true,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
