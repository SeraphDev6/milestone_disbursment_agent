import { lazy, Suspense, type ReactNode } from "react";
import { createBrowserRouter, Outlet, useHref, useNavigate, type NavigateOptions } from "react-router";
import { RouterProvider as AriaRouterProvider } from "react-aria-components";
import { Announcer, Busy, ToastRegion } from "@/design/components";
import { AppLayout, BareLayout } from "./Layout";
import { RouteError } from "./ErrorBoundary";
import { RouteAnnouncer } from "./RouteAnnouncer";
import { AgreementRoute, HomeRedirect, OrgRoute, RequireSession } from "./guards";
import { SignIn } from "@/features/auth/SignIn";
import { Verify } from "@/features/auth/Verify";
import { SSODone } from "@/features/auth/SSODone";
import { Welcome } from "@/features/auth/Welcome";
import { AcceptInvitation } from "@/features/auth/AcceptInvitation";
import { Home } from "@/features/home/Home";
import { NewAgreement } from "@/features/draft/NewAgreement";
import { AgreementLayout } from "@/features/agreement/AgreementLayout";
import { Overview } from "@/features/agreement/Overview";
import { Criteria } from "@/features/agreement/Criteria";
import { Parties } from "@/features/agreement/Parties";
import { Ledger } from "@/features/agreement/Ledger";
import { Activity } from "@/features/agreement/Activity";
import { Integrations } from "@/features/agreement/Integrations";
import { Packet } from "@/features/agreement/Packet";
import { Tranche } from "@/features/tranche/Tranche";
import { OrgSettings } from "@/features/org/OrgSettings";
import { Account } from "@/features/account/Account";

// Heavier, less-visited pages load on demand: the chart library and the design-system specimen.
const Portfolio = lazy(() => import("@/features/portfolio/Portfolio").then((m) => ({ default: m.Portfolio })));
const Demo = lazy(() => import("@/features/demo/Demo").then((m) => ({ default: m.Demo })));
const Specimen = lazy(() => import("@/features/design/Specimen").then((m) => ({ default: m.Specimen })));
const later = (node: ReactNode) => <Suspense fallback={<Busy label="Loading" />}>{node}</Suspense>;

declare module "react-aria-components" {
  interface RouterConfig {
    routerOptions: NavigateOptions;
  }
}

/** Root: lets React Aria links navigate client-side, and mounts the app-wide live regions. */
function Root() {
  const navigate = useNavigate();
  return (
    <AriaRouterProvider navigate={navigate} useHref={useHref}>
      <RouteAnnouncer />
      <Announcer />
      <ToastRegion />
      <Outlet />
    </AriaRouterProvider>
  );
}

export const router = createBrowserRouter(
  [
    {
      element: <Root />,
      errorElement: <RouteError />,
      children: [
        {
          element: <BareLayout />,
          children: [
            { path: "sign-in", element: <SignIn /> },
            { path: "auth/verify", element: <Verify /> },
            { path: "auth/sso", element: <SSODone /> },
          ],
        },
        {
          element: <RequireSession />,
          children: [
            {
              element: <AppLayout />,
              errorElement: <RouteError />,
              children: [
                { index: true, element: <HomeRedirect /> },
                { path: "welcome", element: <Welcome /> },
                { path: "invitations/accept", element: <AcceptInvitation /> },
                { path: "account", element: <Account /> },
                {
                  path: "o/:orgId",
                  element: <OrgRoute />,
                  children: [
                    { index: true, element: <Home /> },
                    { path: "portfolio", element: later(<Portfolio />) },
                    { path: "new", element: <NewAgreement /> },
                    { path: "settings", element: <OrgSettings /> },
                    {
                      path: "a/:agreementId",
                      element: <AgreementRoute />,
                      children: [
                        { path: "m/:milestoneId", element: <Tranche /> },
                        {
                          element: <AgreementLayout />,
                          children: [
                            { index: true, element: <Overview /> },
                            { path: "criteria", element: <Criteria /> },
                            { path: "parties", element: <Parties /> },
                            { path: "ledger", element: <Ledger /> },
                            { path: "activity", element: <Activity /> },
                            { path: "integrations", element: <Integrations /> },
                            { path: "packet", element: <Packet /> },
                          ],
                        },
                      ],
                    },
                  ],
                },
              ],
            },
          ],
        },
        // Public pages: the design specimen (reviewable and axe-scanned without an account) and the mock-mode demo,
        // which signs its own people in.
        { element: <AppLayout />, children: [
            { path: "design", element: later(<Specimen />) },
            { path: "demo", element: later(<Demo />) },
          ] },
        { path: "*", element: <BareLayout><RouteError /></BareLayout> },
      ],
    },
  ],
  { basename: import.meta.env.BASE_URL.replace(/\/$/, "") },
);
