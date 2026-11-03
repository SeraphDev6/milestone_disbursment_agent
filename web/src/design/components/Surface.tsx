import type { ReactNode } from "react";
import {
  Disclosure as AriaDisclosure,
  DisclosurePanel,
  Heading,
  Button as AriaButton,
  Meter as AriaMeter,
  Breadcrumbs as AriaBreadcrumbs,
  Breadcrumb,
  Link,
} from "react-aria-components";
import { Glyph, type GlyphName } from "./Glyph";
import s from "./Surface.module.css";

export const layout = s;

/** A sheet of paper on the desk: the basic content surface. */
export function Sheet({
  title, titleLevel = 2, actions, children, tight, id, labelledBy, className,
}: { title?: ReactNode; titleLevel?: 2 | 3; actions?: ReactNode; children: ReactNode; tight?: boolean; id?: string; labelledBy?: string; className?: string }) {
  const H = titleLevel === 2 ? "h2" : "h3";
  const headingId = id ? `${id}-title` : undefined;
  return (
    <section className={`${s.sheet} ${tight ? s.sheetTight : ""} ${className ?? ""}`} id={id} aria-labelledby={labelledBy ?? (title ? headingId : undefined)}>
      {(title || actions) && (
        <div className={s.sheetHead}>
          {title && <H id={headingId}>{title}</H>}
          {actions && <div className={s.sheetActions}>{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

/** Page title block. The h1 is focused on navigation (see RouteAnnouncer), hence tabIndex -1. */
export function PageHeader({ eyebrow, title, stamp, lede, meta, actions, crumbs }: { eyebrow?: ReactNode; title: ReactNode; stamp?: ReactNode; lede?: ReactNode; meta?: ReactNode; actions?: ReactNode; crumbs?: ReactNode }) {
  return (
    <header className={s.pageHeader}>
      {crumbs}
      {eyebrow && <div className={s.eyebrow}>{eyebrow}</div>}
      <div className={s.titleRow}>
        <h1 tabIndex={-1} data-page-title>{title}</h1>
        {stamp}
      </div>
      {lede && <p className={s.lede}>{lede}</p>}
      {meta && <div className={s.headerMeta}>{meta}</div>}
      {actions && <div className={s.headerActions}>{actions}</div>}
    </header>
  );
}

export function Crumbs({ items }: { items: { label: string; href?: string }[] }) {
  return (
    <AriaBreadcrumbs className={s.crumbs} aria-label="Breadcrumbs">
      {items.map((i, n) => (
        <Breadcrumb key={n} id={n}>
          {i.href && n < items.length - 1 ? <Link href={i.href}>{i.label}</Link> : <Link aria-current="page">{i.label}</Link>}
        </Breadcrumb>
      ))}
    </AriaBreadcrumbs>
  );
}

export function Facts({ items, label }: { items: [ReactNode, ReactNode][]; label?: string }) {
  return (
    <dl className={s.dl} aria-label={label}>
      {items.map(([k, v], i) => (
        <div key={i} style={{ display: "contents" }}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export type Tone = "info" | "success" | "warning" | "error" | "note";
const TONE_GLYPH: Record<Tone, GlyphName> = { info: "info", success: "check", warning: "warning", error: "warning", note: "info" };

/**
 * A notice. `live` makes it a status message that's announced when it appears (errors assertively).
 * Static explanations should leave `live` off so they aren't read out on every render.
 */
export function Callout({ tone = "info", title, children, actions, live, id }: { tone?: Tone; title?: ReactNode; children?: ReactNode; actions?: ReactNode; live?: boolean; id?: string }) {
  const role = live ? (tone === "error" ? "alert" : "status") : undefined;
  return (
    <div className={`${s.callout} ${s[tone]}`} role={role} id={id} tabIndex={id ? -1 : undefined}>
      <Glyph name={TONE_GLYPH[tone]} size={18} />
      <div className={s.calloutBody}>
        {title && <p className={s.calloutTitle}>{title}</p>}
        {children}
      </div>
      {actions && <div className={s.calloutActions}>{actions}</div>}
    </div>
  );
}

/** Why an action isn't available to you. Keep the button visible and say this next to it. */
export function PermissionNote({ children, id }: { children: ReactNode; id?: string }) {
  return (
    <p className={s.permission} id={id}>
      <Glyph name="key" size={14} />
      <span>{children}</span>
    </p>
  );
}

export function Disclosure({ title, children, defaultExpanded }: { title: ReactNode; children: ReactNode; defaultExpanded?: boolean }) {
  return (
    <AriaDisclosure className={s.disclosure} defaultExpanded={defaultExpanded}>
      <Heading level={3} style={{ margin: 0, fontSize: "var(--text-md)", fontFamily: "var(--font-ui)" }}>
        <AriaButton slot="trigger" className={s.disclosureTrigger}>
          <Glyph name="chevronRight" />
          {title}
        </AriaButton>
      </Heading>
      <DisclosurePanel className={s.disclosurePanel}>{children}</DisclosurePanel>
    </AriaDisclosure>
  );
}

/** Confidence against a threshold: the bar is hatched below the line so it doesn't rely on colour. */
export function ThresholdMeter({ label, value, threshold }: { label: string; value: number; threshold: number }) {
  const pct = Math.round(value * 100);
  const below = value < threshold;
  return (
    <AriaMeter className={s.meter} aria-label={label} value={pct} minValue={0} maxValue={100} valueLabel={`${pct}%, ${below ? "below" : "at or above"} the ${Math.round(threshold * 100)}% auto-release threshold`}>
      {({ percentage }) => (
        <>
          <div className={s.meterHead}>
            <span className={s.meterLabel}>{label}</span>
            <span className={s.meterValue}>{pct}% {below ? "· below threshold" : "· meets threshold"}</span>
          </div>
          <div className={s.meterTrack} aria-hidden="true">
            <div className={`${s.meterFill} ${below ? s.meterFillBelow : ""}`} style={{ width: `${percentage}%` }} />
            <div className={s.meterMark} style={{ left: `calc(${threshold * 100}% - 1px)` }} />
          </div>
          <span className={s.meterMarkLabel} aria-hidden="true">Threshold {Math.round(threshold * 100)}%</span>
        </>
      )}
    </AriaMeter>
  );
}

export function EmptyState({ title, children, actions }: { title: ReactNode; children?: ReactNode; actions?: ReactNode }) {
  return (
    <div className={s.empty}>
      <p className={s.emptyTitle}>{title}</p>
      {children && <p>{children}</p>}
      {actions && <div className={s.emptyActions}>{actions}</div>}
    </div>
  );
}

/** Loading state. Announces once via role=status; the shimmer-free skeleton keeps layout from jumping. */
export function Busy({ label = "Loading", lines = 0 }: { label?: string; lines?: number }) {
  return (
    <div role="status" aria-live="polite">
      <div className={s.busy}>
        <span className="sr-only">{label}…</span>
        <span aria-hidden="true">{label}…</span>
      </div>
      {lines > 0 && (
        <div className={s.skeleton} aria-hidden="true">
          {Array.from({ length: lines }, (_, i) => (
            <div key={i} className={s.skeletonLine} style={{ width: `${90 - ((i * 17) % 40)}%` }} />
          ))}
        </div>
      )}
    </div>
  );
}
