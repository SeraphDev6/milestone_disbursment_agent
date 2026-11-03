/**
 * Confirmation toasts for completed actions. They're announced through the shared live region, stay at
 * least 8 seconds (longer if hovered or focused), and are never the only place an outcome is shown.
 */
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { announce } from "./Announcer";
import { Glyph, type GlyphName } from "./Glyph";
import s from "./Toast.module.css";

type Toast = { id: number; title: string; body?: string; glyph: GlyphName };
let items: Toast[] = [];
let next = 1;
const subs = new Set<() => void>();
const emit = () => subs.forEach((f) => f());

export function toast(title: string, opts: { body?: string; tone?: "success" | "info" | "error" } = {}) {
  const glyph: GlyphName = opts.tone === "error" ? "warning" : opts.tone === "info" ? "info" : "check";
  items = [...items, { id: next++, title, body: opts.body, glyph }].slice(-4);
  announce(opts.body ? `${title}. ${opts.body}` : title, opts.tone === "error" ? "assertive" : "polite");
  emit();
}

/** Drop every toast, e.g. when another person takes over the screen. */
export function clearToasts() {
  items = [];
  emit();
}

function dismiss(id: number) {
  items = items.filter((t) => t.id !== id);
  emit();
}

function Item({ t }: { t: Toast }) {
  const [paused, setPaused] = useState(false);
  const left = useRef(8000);
  useEffect(() => {
    if (paused) return;
    const start = Date.now();
    const timer = setTimeout(() => dismiss(t.id), left.current);
    return () => {
      clearTimeout(timer);
      left.current -= Date.now() - start;
    };
  }, [paused, t.id]);
  return (
    // Pausing on hover/focus is a courtesy (WCAG 2.2.1); the toast itself isn't a control.
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
    <li className={s.toast} onMouseEnter={() => setPaused(true)} onMouseLeave={() => setPaused(false)} onFocus={() => setPaused(true)} onBlur={() => setPaused(false)}>
      <Glyph name={t.glyph} size={18} />
      <div>
        <p className={s.title}>{t.title}</p>
        {t.body && <p>{t.body}</p>}
      </div>
      <button type="button" className={s.close} aria-label={`Dismiss: ${t.title}`} onClick={() => dismiss(t.id)}>
        <Glyph name="close" />
      </button>
    </li>
  );
}

export function ToastRegion() {
  const list = useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => subs.delete(f);
    },
    () => items,
  );
  // Visual only: the live region in <Announcer> already spoke these.
  return (
    <ul className={s.region} aria-label="Notifications">
      {list.map((t) => <Item key={t.id} t={t} />)}
    </ul>
  );
}
