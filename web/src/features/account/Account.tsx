import { Link, useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { signOutToken, useMe } from "@/api/queries";
import { sessions, useSessions } from "@/auth/session";
import { useTitle } from "@/app/RouteAnnouncer";
import { paths } from "@/app/context";
import { Button, ButtonLink, Facts, layout, PageHeader, RadioGroup, Sheet } from "@/design/components";
import { setTheme, THEME_OPTIONS, useTheme, type ThemeChoice } from "@/design/theme";

export function Account() {
  useTitle("Account");
  const me = useMe();
  const theme = useTheme();
  const { sessions: all, activeId } = useSessions();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const user = me.data?.user;
  async function signOutAll() {
    await Promise.all(all.map((s) => signOutToken(s.token)));
    sessions.clear();
    qc.clear();
    navigate("/sign-in");
  }
  return (
    <div className={layout.stackLg}>
      <PageHeader title="Account" lede="Your sign-in, your organizations, and how the app looks." />
      <div className={layout.grid2}>
        <Sheet title="You">
          <Facts items={[["Email", user?.email ?? "…"], ["Name", user?.name ?? "Not set"]]} />
        </Sheet>
        <Sheet title="Organizations">
          <ul style={{ margin: 0 }}>
            {me.data?.memberships.map((m) => (
              <li key={m.org_id}><Link to={paths.org(m.org_id)}>{m.org_name}</Link> · {m.role}</li>
            ))}
          </ul>
          <div style={{ marginTop: "var(--space-3)" }}><ButtonLink href="/welcome?new=1" icon="plus">Create an organization</ButtonLink></div>
        </Sheet>
      </div>
      <Sheet title="Appearance">
        <RadioGroup label="Theme" value={theme} onChange={(v) => setTheme(v as ThemeChoice)} options={THEME_OPTIONS} description="Every theme meets WCAG 2.2 AA contrast; Contrast meets AAA for text." />
      </Sheet>
      <Sheet title="Sessions in this browser">
        <ul style={{ margin: "0 0 var(--space-3)" }}>
          {all.map((s) => (
            <li key={s.user.id}>
              {s.user.email} {s.user.id === activeId && <strong>(active)</strong>} · {s.persistent ? "kept on this device" : "this tab only"}
            </li>
          ))}
        </ul>
        <div className={layout.row}>
          <ButtonLink href="/sign-in?add=1" icon="plus">Sign in another person</ButtonLink>
          <Button variant="danger" icon="signOut" onPress={signOutAll}>Sign everyone out</Button>
        </div>
      </Sheet>
    </div>
  );
}
