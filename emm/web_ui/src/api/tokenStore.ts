/**
 * Holds the access token in memory for the ApiClient.
 *
 * - `access_token` lives in memory only; a hard reload requires a refresh
 *   round-trip (or a re-login) to re-issue.
 * - `refresh_token` is **not** stored in JS-visible storage. Backend ставит
 *   его HttpOnly Secure cookie (`dbos_refresh`, path=/api/auth/v1) — браузер
 *   шлёт его автоматически на refresh/logout, а JS его не видит. Это убирает
 *   XSS-вектор на refresh (раньше токен лежал в sessionStorage).
 *
 * UI components should not read these values directly; go through AuthContext
 * which owns the lifecycle. The store exposes a tiny subscriber API so that
 * AuthContext can react to ApiClient-driven token rotation (e.g. forced
 * `signOut` after a refresh failure).
 */

let accessToken: string | null = null;
const listeners = new Set<() => void>();

function notify(): void {
  listeners.forEach((cb) => {
    try {
      cb();
    } catch {
      // listener errors must not stop subsequent listeners
    }
  });
}

export function setTokens(access: string, _refresh?: string): void {
  // `_refresh` принимаем для обратной совместимости с auth/auth.ts — но
  // не храним: refresh живёт в HttpOnly cookie, поставленной бэком.
  accessToken = access;
  notify();
}

export function setAccessToken(access: string | null): void {
  accessToken = access;
  notify();
}

export function getAccessToken(): string | null {
  return accessToken;
}

/**
 * Cookie с refresh-токеном — HttpOnly, JS его не видит и не должен.
 * Возвращаем null всегда; вызывающий код использует endpoint /refresh
 * напрямую и полагается на cookie, которую браузер прикрепит сам.
 */
export function getRefreshToken(): string | null {
  return null;
}

export function clearTokens(): void {
  accessToken = null;
  notify();
}

/** Subscribe to token mutations. Returns an unsubscribe callback. */
export function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}
