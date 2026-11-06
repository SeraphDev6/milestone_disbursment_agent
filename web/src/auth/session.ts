/**
 * Signed-in sessions. A session is a bearer token from the magic link (valid 30 days on the server).
 *
 * Several sessions can be held at once ("switch person"): the demo uses this to play the funder, the grantee
 * and the reviewer from one browser. "Keep me signed in" decides localStorage vs sessionStorage.
 *
 * Tokens in web storage are readable by any script on the page, so the app ships no third-party scripts
 * and a strict CSP is recommended in production (see DESIGN.md, "Security notes").
 */
import { useSyncExternalStore } from "react";
import type { UserRead } from "@/api/types";

export type Session = { token: string; user: UserRead; persistent: boolean; addedAt: string };

const KEY = "mde.sessions";
const ACTIVE = "mde.active";

function read(store: Storage | undefined): Session[] {
  try {
    const raw = store?.getItem(KEY);
    return raw ? (JSON.parse(raw) as Session[]) : [];
  } catch {
    return [];
  }
}

function safe<T>(fn: () => T, fallback: T): T {
  try {
    return fn();
  } catch {
    return fallback;
  }
}

const local = () => safe(() => window.localStorage, undefined);
const tab = () => safe(() => window.sessionStorage, undefined);

type State = { sessions: Session[]; activeId: string | null };
let state: State = load();
const listeners = new Set<() => void>();

function load(): State {
  const sessions = [...read(local()), ...read(tab())];
  const activeId = safe(() => tab()?.getItem(ACTIVE) ?? local()?.getItem(ACTIVE) ?? null, null);
  return { sessions, activeId: sessions.some((s) => s.user.id === activeId) ? activeId : (sessions[0]?.user.id ?? null) };
}

function save() {
  const persistent = state.sessions.filter((s) => s.persistent);
  const transient = state.sessions.filter((s) => !s.persistent);
  safe(() => local()?.setItem(KEY, JSON.stringify(persistent)), undefined);
  safe(() => tab()?.setItem(KEY, JSON.stringify(transient)), undefined);
  safe(() => {
    if (state.activeId) {
      local()?.setItem(ACTIVE, state.activeId);
      tab()?.setItem(ACTIVE, state.activeId);
    } else {
      local()?.removeItem(ACTIVE);
      tab()?.removeItem(ACTIVE);
    }
  }, undefined);
}

function emit() {
  for (const l of listeners) l();
}

function set(next: State) {
  state = next;
  save();
  emit();
}

// Another tab signed in or out: follow it.
if (typeof window !== "undefined") {
  window.addEventListener("storage", (e) => {
    if (e.key === KEY || e.key === ACTIVE) {
      state = load();
      emit();
    }
  });
}

export const sessions = {
  subscribe(l: () => void) {
    listeners.add(l);
    return () => listeners.delete(l);
  },
  get: () => state,
  active(): Session | null {
    return state.sessions.find((s) => s.user.id === state.activeId) ?? null;
  },
  token(): string | null {
    return sessions.active()?.token ?? null;
  },
  add(token: string, user: UserRead, persistent: boolean) {
    const rest = state.sessions.filter((s) => s.user.id !== user.id);
    set({ sessions: [...rest, { token, user, persistent, addedAt: new Date().toISOString() }], activeId: user.id });
  },
  switchTo(userId: string) {
    if (state.sessions.some((s) => s.user.id === userId)) set({ ...state, activeId: userId });
  },
  /** Drop one session (sign-out, or the server said the token is no longer valid). */
  remove(userId: string) {
    const remaining = state.sessions.filter((s) => s.user.id !== userId);
    set({ sessions: remaining, activeId: state.activeId === userId ? (remaining[0]?.user.id ?? null) : state.activeId });
  },
  clear() {
    set({ sessions: [], activeId: null });
  },
};

export function useSessions(): State {
  return useSyncExternalStore(sessions.subscribe, sessions.get, sessions.get);
}

export function useActiveSession(): Session | null {
  const s = useSessions();
  return s.sessions.find((x) => x.user.id === s.activeId) ?? null;
}

/** Remembers which org a person last acted for, per person. */
export const lastOrg = {
  get: (userId: string) => safe(() => local()?.getItem(`mde.org.${userId}`) ?? null, null),
  set: (userId: string, orgId: string) => safe(() => local()?.setItem(`mde.org.${userId}`, orgId), undefined),
};
