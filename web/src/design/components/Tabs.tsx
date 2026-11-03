import type { ReactNode } from "react";
import { Tab, TabList, TabPanel, Tabs as AriaTabs, type Key } from "react-aria-components";
import { NavLink } from "react-router";
import s from "./Surface.module.css";

export type TabDef = { id: string; label: ReactNode; content: ReactNode; isDisabled?: boolean };

/** In-page tabs (arrow keys move between tabs; Tab moves into the panel). */
export function Tabs({ label, tabs, selected, onSelect, defaultSelected }: { label: string; tabs: TabDef[]; selected?: string; onSelect?: (id: string) => void; defaultSelected?: string }) {
  return (
    <AriaTabs className={s.tabs} selectedKey={selected} defaultSelectedKey={defaultSelected} onSelectionChange={(k: Key) => onSelect?.(String(k))} disabledKeys={tabs.filter((t) => t.isDisabled).map((t) => t.id)}>
      <TabList aria-label={label} className={s.tabList}>
        {tabs.map((t) => (
          <Tab key={t.id} id={t.id} className={s.tab}>{t.label}</Tab>
        ))}
      </TabList>
      {tabs.map((t) => (
        <TabPanel key={t.id} id={t.id} className={s.tabPanel}>{t.content}</TabPanel>
      ))}
    </AriaTabs>
  );
}

/** Section navigation between routes. Looks like tabs, behaves like links (aria-current="page"). */
export function RouteTabs({ label, links }: { label: string; links: { to: string; label: ReactNode; end?: boolean; badge?: ReactNode }[] }) {
  return (
    <nav aria-label={label}>
      <div className={s.tabList}>
        {links.map((l) => (
          <NavLink key={l.to} to={l.to} end={l.end} className={s.tab}>
            {l.label}
            {l.badge}
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
