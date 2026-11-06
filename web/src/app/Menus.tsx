/** Header menus: acting org, person (sessions), and theme. All are React Aria menus: arrow keys, typeahead, Escape. */
import type { ReactNode } from "react";
import { Button as AriaButton, Header, Menu, MenuItem, MenuSection, MenuTrigger, Popover, Separator, Text } from "react-aria-components";
import { useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import type { Me } from "@/api/types";
import { signOutToken } from "@/api/queries";
import { sessions, useSessions } from "@/auth/session";
import { clearToasts, Glyph, type GlyphName } from "@/design/components";
import { setTheme, THEME_OPTIONS, useTheme, type ThemeChoice } from "@/design/theme";
import { paths } from "./context";
import s from "./Layout.module.css";

function Trigger({ icon, label, hint, aria }: { icon: GlyphName; label: ReactNode; hint?: ReactNode; aria: string }) {
  return (
    <AriaButton className={s.menuTrigger} aria-label={aria}>
      <Glyph name={icon} />
      <span className={s.triggerText}>
        {label}
        {hint && <span className={s.triggerRole}> · {hint}</span>}
      </span>
      <Glyph name="chevronDown" size={12} />
    </AriaButton>
  );
}

export function OrgMenu({ me, orgId }: { me: Me; orgId?: string }) {
  const navigate = useNavigate();
  const current = me.memberships.find((m) => m.org_id === orgId);
  return (
    <MenuTrigger>
      <Trigger
        icon="org"
        label={current?.org_name ?? "Choose organization"}
        hint={current?.role}
        aria={current ? `${current.org_name} · ${current.role}: acting organization, switch` : "Choose organization"}
      />
      <Popover className={s.popover} placement="bottom end">
        <Menu
          className={s.menu}
          aria-label="Organizations"
          selectionMode="single"
          selectedKeys={orgId ? [orgId] : []}
          onAction={(key) => {
            const k = String(key);
            if (k === "__new") navigate("/welcome?new=1");
            else navigate(paths.org(k));
          }}
        >
          <MenuSection>
            <Header className={s.sectionHeader}>Act for</Header>
            {me.memberships.map((m) => (
              <MenuItem key={m.org_id} id={m.org_id} className={s.menuItem} textValue={m.org_name}>
                <Glyph name="check" className={s.itemCheck} />
                <Text slot="label">{m.org_name}</Text>
                <Text slot="description" className={s.itemHint}>{m.role}</Text>
              </MenuItem>
            ))}
          </MenuSection>
          <Separator className={s.separator} />
          <MenuItem id="__new" className={s.menuItem} textValue="Create an organization">
            <Glyph name="plus" />
            <Text slot="label">Create an organization</Text>
          </MenuItem>
        </Menu>
      </Popover>
    </MenuTrigger>
  );
}

export function PersonMenu({ me, demo }: { me: Me; demo: boolean }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { sessions: all, activeId } = useSessions();
  const others = all.filter((x) => x.user.id !== activeId);
  async function signOut() {
    const active = sessions.active();
    if (active) {
      await signOutToken(active.token);
      sessions.remove(active.user.id);
    }
    qc.clear();
    navigate(sessions.active() ? "/" : "/sign-in");
  }
  return (
    <MenuTrigger>
      <Trigger icon="person" label={me.user.name || me.user.email} aria={`${me.user.name || me.user.email}: account menu`} />
      <Popover className={s.popover} placement="bottom end">
        <Menu
          className={s.menu}
          aria-label="Account"
          onAction={(key) => {
            const k = String(key);
            if (k === "__account") navigate("/account");
            else if (k === "__add") navigate("/sign-in?add=1");
            else if (k === "__out") void signOut();
            else {
              sessions.switchTo(k);
              qc.clear();
              clearToasts();
              navigate("/");
            }
          }}
        >
          <MenuSection>
            <Header className={s.sectionHeader}>Signed in</Header>
            <MenuItem id="__account" className={s.menuItem} textValue="Account and appearance">
              <Glyph name="person" />
              <Text slot="label">{me.user.email}</Text>
              <Text slot="description" className={s.itemHint}>Account and appearance</Text>
            </MenuItem>
          </MenuSection>
          {(others.length > 0 || demo) && (
            <>
              <Separator className={s.separator} />
              <MenuSection>
                <Header className={s.sectionHeader}>Switch person</Header>
                {others.map((o) => (
                  <MenuItem key={o.user.id} id={o.user.id} className={s.menuItem} textValue={o.user.email}>
                    <Glyph name="arrow" />
                    <Text slot="label">{o.user.email}</Text>
                  </MenuItem>
                ))}
                <MenuItem id="__add" className={s.menuItem} textValue="Sign in another person">
                  <Glyph name="plus" />
                  <Text slot="label">Sign in another person</Text>
                  {demo && <Text slot="description" className={s.itemHint}>Demo: play every side in one browser</Text>}
                </MenuItem>
              </MenuSection>
            </>
          )}
          <Separator className={s.separator} />
          <MenuItem id="__out" className={s.menuItem} textValue="Sign out">
            <Glyph name="signOut" />
            <Text slot="label">Sign out</Text>
          </MenuItem>
        </Menu>
      </Popover>
    </MenuTrigger>
  );
}

const THEME_GLYPH: Record<ThemeChoice, GlyphName> = { system: "half", paper: "sun", carbon: "moon", contrast: "contrast" };

export function ThemeMenu() {
  const theme = useTheme();
  const current = THEME_OPTIONS.find((t) => t.id === theme)!;
  return (
    <MenuTrigger>
      <AriaButton className={s.menuTrigger} aria-label={`Theme: ${current.label}. Change theme`}>
        <Glyph name={THEME_GLYPH[theme]} />
        <span className={`${s.triggerText} sr-only`}>{current.label}</span>
      </AriaButton>
      <Popover className={s.popover} placement="bottom end">
        <Menu className={s.menu} aria-label="Theme" selectionMode="single" selectedKeys={[theme]} onAction={(k) => setTheme(k as ThemeChoice)}>
          {THEME_OPTIONS.map((t) => (
            <MenuItem key={t.id} id={t.id} className={s.menuItem} textValue={t.label}>
              <Glyph name="check" className={s.itemCheck} />
              <Text slot="label">{t.label}</Text>
              <Text slot="description" className={s.itemHint}>{t.description}</Text>
            </MenuItem>
          ))}
        </Menu>
      </Popover>
    </MenuTrigger>
  );
}
