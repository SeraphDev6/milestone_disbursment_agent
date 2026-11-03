import type { ReactNode } from "react";
import { NavLink, Outlet, useParams } from "react-router";
import { isMock, useHealth, useMe } from "@/api/queries";
import { Glyph } from "@/design/components";
import { paths } from "./context";
import { OrgMenu, PersonMenu, ThemeMenu } from "./Menus";
import s from "./Layout.module.css";

export function MockRibbon() {
  const health = useHealth();
  if (!isMock(health.data)) return null;
  return (
    <div className={s.ribbon} role="note" aria-label="Mock mode">
      <span>Mock mode</span>
      <span aria-hidden="true">·</span>
      <span>No real money moves</span>
      <span aria-hidden="true">·</span>
      <span>Sign-in and invitation links appear on screen instead of email</span>
      <span aria-hidden="true">·</span>
      <NavLink to="/demo" className={s.ribbonLink}>Demo</NavLink>
    </div>
  );
}

function Footer() {
  return (
    <footer className={s.footer}>
      <div className={s.footerInner}>
        <span>Milestone Disbursement Engine · AGPL-3.0</span>
        <a href="/docs">API reference</a>
        <NavLink to="/design">Design system</NavLink>
      </div>
    </footer>
  );
}

/** Signed-in chrome: header with navigation, org/person/theme menus, then the page. */
export function AppLayout() {
  const { orgId } = useParams();
  const me = useMe();
  const health = useHealth();
  const mock = isMock(health.data);
  return (
    <>
      <a className="skip-link" href="#main">Skip to content</a>
      <MockRibbon />
      <header className={s.header}>
        <div className={s.headerInner}>
          <NavLink to={orgId ? paths.org(orgId) : "/"} className={s.brand}>
            <span>Milestone Ledger</span>
            <span className="sr-only">, home</span>
            <span className={s.brandMark} aria-hidden="true">№ MDE</span>
          </NavLink>
          {orgId && (
            <nav className={s.nav} aria-label="Primary">
              <NavLink end to={paths.org(orgId)} className={s.navLink}><Glyph name="inbox" />Home</NavLink>
              <NavLink to={paths.portfolio(orgId)} className={s.navLink}><Glyph name="ledger" />Portfolio</NavLink>
              <NavLink to={paths.newAgreement(orgId)} className={s.navLink}><Glyph name="plus" />New agreement</NavLink>
              <NavLink to={paths.settings(orgId)} className={s.navLink}><Glyph name="org" />Organization</NavLink>
            </nav>
          )}
          <div className={s.tools}>
            {me.data && me.data.memberships.length > 0 && <OrgMenu me={me.data} orgId={orgId} />}
            {me.data && <PersonMenu me={me.data} mock={mock} />}
            <ThemeMenu />
          </div>
        </div>
      </header>
      <main id="main" className={s.main} tabIndex={-1}>
        <Outlet />
      </main>
      <Footer />
    </>
  );
}

/** Chrome for pages before sign-in: narrow column, skip link, ribbon, theme toggle. */
export function BareLayout({ children }: { children?: ReactNode }) {
  return (
    <>
      <a className="skip-link" href="#main">Skip to content</a>
      <MockRibbon />
      <header className={s.header}>
        <div className={s.headerInner}>
          <span className={s.brand}>
            Milestone Ledger <span className={s.brandMark} aria-hidden="true">№ MDE</span>
          </span>
          <div className={s.tools}><ThemeMenu /></div>
        </div>
      </header>
      <main id="main" className={s.bare} tabIndex={-1}>
        {children ?? <Outlet />}
      </main>
      <Footer />
    </>
  );
}
