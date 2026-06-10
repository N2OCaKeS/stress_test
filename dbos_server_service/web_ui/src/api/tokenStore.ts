/**
 * Holds access + refresh tokens for the ApiClient.
 *
 * - `access_token` lives in memory only; a hard reload requires a refresh
 *   round-trip (or a re-login) to re-issue.
 * - `refresh_token` is persisted to `sessionStorage` so that an accidental
 *   tab reload during a working session keeps the user logged in until the
 *   tab is closed. Choosing `sessionStorage` over `localStorage` keeps the
 *   token bound to the current browsing context — closing the tab kills it.
 *
 * UI components should not read these values directly; go through AuthContext
 * which owns the lifecycle. The store exposes a tiny subscriber API so that
 * AuthContext can react to ApiClient-driven token rotation (e.g. forced
 * `signOut` after a refresh failure).
 */

const REFRESH_STORAGE_KEY = "dbos.refresh";

let accessToken: string | null = null;
const listeners = new Set<() => void>();

function readRefresh(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(REFRESH_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeRefresh(value: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (value === null) {
      window.sessionStorage.removeItem(REFRESH_STORAGE_KEY);
    } else {
      window.sessionStorage.setItem(REFRESH_STORAGE_KEY, value);
    }
  } catch {
    // sessionStorage may be unavailable (private mode, quota). Tokens stay
    // in-memory; reload will require re-login.
  }
}

function notify(): void {
  listeners.forEach((cb) => {
    try {
      cb();
    } catch {
      // listener errors must not stop subsequent listeners
    }
  });
}

export function setTokens(access: string, refresh: string): void {
  accessToken = access;
  writeRefresh(refresh);
  notify();
}

export function setAccessToken(access: string | null): void {
  accessToken = access;
  notify();
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function getRefreshToken(): string | null {
  return readRefresh();
}

export function clearTokens(): void {
  accessToken = null;
  writeRefresh(null);
  notify();
}

/** Subscribe to token mutations. Returns an unsubscribe callback. */
export function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}
