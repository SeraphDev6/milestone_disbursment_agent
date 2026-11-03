/**
 * Single-page navigation is silent to screen readers. On every route change this moves focus to the new
 * page's h1 (or main) and announces the document title, the way a full page load would.
 */
import { useEffect, useRef } from "react";
import { useLocation } from "react-router";
import { announce } from "@/design/components";

export function RouteAnnouncer() {
  const { pathname } = useLocation();
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    // Wait for the new page's heading (lazy pages and data take a moment), then focus it and announce the title.
    // Falls back to <main> after a second so focus never stays on a link that no longer exists.
    let tries = 0;
    let t: ReturnType<typeof setTimeout>;
    const attempt = () => {
      const h1 = document.querySelector<HTMLElement>("main h1[data-page-title]") ?? document.querySelector<HTMLElement>("main h1");
      if (!h1 && tries++ < 20) {
        t = setTimeout(attempt, 50);
        return;
      }
      const target = h1 ?? document.getElementById("main");
      if (target) {
        if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
        target.focus({ preventScroll: false });
      }
      announce(document.title);
    };
    t = setTimeout(attempt, 50);
    return () => clearTimeout(t);
  }, [pathname]);
  return null;
}

/** Set the document title ("Tranche 1 · Riverside Literacy Grant · Milestone Ledger"). */
export function useTitle(...parts: (string | undefined | null | false)[]) {
  const title = [...parts.filter(Boolean), "Milestone Ledger"].join(" · ");
  useEffect(() => {
    document.title = title;
  }, [title]);
}
