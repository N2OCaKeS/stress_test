import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  ApiError,
  registerPasswordChangeRequiredHandler,
  registerSignOutHandler,
} from "@/api/client";
import * as authApi from "@/api/auth/auth";
import {
  clearTokens,
  setTokens,
  subscribe as subscribeTokens,
} from "@/api/tokenStore";
import { closeAllConsoleSockets } from "@/lib/consoleSocketRegistry";
import type {
  IdentityContext,
  LoginRequest,
  MeResponse,
} from "@/api/auth/types";
import { ForcePasswordChangeModal } from "@/components/ForcePasswordChangeModal";

/**
 * Mock-auth toggle (env-gated). Default `false`. The persona-picker UI lives
 * behind this flag — useful for offline UI work without a backend.
 */
export const USE_MOCK_AUTH: boolean =
  (import.meta.env.VITE_USE_MOCK_AUTH as string | undefined) === "true";

export interface AuthContextValue {
  user: IdentityContext | null;
  /**
   * Initial-load state. True while we are bootstrapping the session from a
   * persisted refresh token; consumers can render a splash instead of
   * forcing /login.
   */
  isLoading: boolean;
  isAuthenticated: boolean;
  /**
   * Backend-driven flag: user must change their password before doing anything
   * else (first login, admin reset, etc.). Mirrors `must_change_password` from
   * `/me`. Consumers render a blocking modal when true and rely on `reload()`
   * to clear it after a successful `POST /users/me/password`.
   */
  mustChangePassword: boolean;
  /**
   * Last login error. Cleared by the next `login()` call. UI surfaces this
   * directly on the login form.
   */
  lastError: ApiError | null;

  login: (req: LoginRequest) => Promise<void>;
  logout: () => Promise<void>;
  /** Force a `/me` re-fetch without going through refresh. */
  reload: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<IdentityContext | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [lastError, setLastError] = useState<ApiError | null>(null);
  // Forced via ApiClient 403 PASSWORD_CHANGE_REQUIRED handler — safety net
  // for the case when /login or /me identity didn't carry the flag.
  const [forcePwdModal, setForcePwdModal] = useState<boolean>(false);

  useEffect(() => {
    registerPasswordChangeRequiredHandler(() => setForcePwdModal(true));
    return () => registerPasswordChangeRequiredHandler(null);
  }, []);

  // ApiClient → context bridge: when the client gives up on refresh it clears
  // tokens and calls back here so we can drop the user out of state. This is
  // the involuntary-signout path too — a revoked session (ban/block/forced
  // password reset — see `_revoke_sessions_on_block` server-side) surfaces
  // here on the next failed refresh, not through `logout()`. Any live
  // interactive console session must not survive under whoever logs in next
  // in this same tab, so we kill them here unconditionally, same as an
  // explicit logout.
  useEffect(() => {
    registerSignOutHandler(() => {
      closeAllConsoleSockets();
      setUser(null);
      try {
        if (typeof window !== "undefined") {
          // We intentionally redirect through window.location because
          // react-router's navigate() is only available inside the tree.
          if (window.location.pathname !== "/login") {
            window.location.assign("/login");
          }
        }
      } catch {
        // ignore
      }
    });
    return () => registerSignOutHandler(null);
  }, []);

  // Keep state in sync with external token mutations (e.g. another tab).
  useEffect(() => {
    return subscribeTokens(() => {
      // Token store mutation alone does not change `user`; we keep this hook
      // for parity with future cross-tab work.
    });
  }, []);

  // Bootstrap: refresh-токен лежит в HttpOnly cookie (JS его не видит), так
  // что guard'ом «есть ли refresh» не воспользоваться. Просто пробуем /me —
  // если в памяти ещё нет access (типовой случай после hard reload), ApiClient
  // сделает /refresh за нас. Cookie прицепится автоматически благодаря path
  // scope. Если cookie тоже нет / истекла — /refresh ответит 4xx и сработает
  // signOut handler, который мы зарегистрировали выше.
  useEffect(() => {
    let cancelled = false;
    async function bootstrap() {
      if (USE_MOCK_AUTH) {
        if (!cancelled) setIsLoading(false);
        return;
      }
      try {
        const me = await authApi.getMe();
        if (!cancelled) setUser(normalizeMe(me));
      } catch (err) {
        if (!cancelled) {
          // PASSWORD_CHANGE_REQUIRED blocks /me but the access token is still
          // valid for POST /users/me/password — keep tokens so the modal can
          // submit. ApiClient already set forcePwdModal=true via the handler.
          const isPwdRequired =
            err instanceof ApiError &&
            err.errorCode === "PASSWORD_CHANGE_REQUIRED";
          if (!isPwdRequired) {
            clearTokens();
            setUser(null);
          }
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }
    bootstrap();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (req: LoginRequest) => {
    setLastError(null);
    setForcePwdModal(false);
    if (USE_MOCK_AUTH) {
      return;
    }
    try {
      const res = await authApi.login(req);
      setTokens(res.access_token, res.refresh_token);
      setUser(normalizeMe(res.identity));
    } catch (err) {
      if (err instanceof ApiError) setLastError(err);
      throw err;
    }
  }, []);

  const logout = useCallback(async () => {
    // Явный logout обрывает все живые консольные SSH-сессии этой вкладки
    // немедленно (close-код 4001 — бэк не входит в grace на этот код, см.
    // `_CLIENT_CLOSE_INTENTIONAL` в `console.py`). Делаем это ДО похода на
    // сервер: сетевой сбой /logout не должен оставить SSH висеть открытым.
    closeAllConsoleSockets();
    if (!USE_MOCK_AUTH) {
      try {
        // Refresh-токен уедет cookie'ой — браузер прикрепит её к запросу.
        await authApi.logout();
      } catch {
        // even if server-side logout fails, drop local session
      }
    }
    clearTokens();
    setUser(null);
    setLastError(null);
    if (typeof window !== "undefined") {
      window.location.assign("/login");
    }
  }, []);

  const reload = useCallback(async () => {
    if (USE_MOCK_AUTH) return;
    // Clear the forced-modal flag first — after a successful password change
    // the user can now reach /me, and any persisted force flag becomes stale.
    setForcePwdModal(false);
    try {
      const me = await authApi.getMe();
      setUser(normalizeMe(me));
    } catch {
      // bootstrap-style failure handled by the sign-out hook
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      isLoading,
      isAuthenticated: user !== null,
      mustChangePassword: (user?.must_change_password ?? false) || forcePwdModal,
      lastError,
      login,
      logout,
      reload,
    }),
    [user, isLoading, lastError, forcePwdModal, login, logout, reload],
  );

  return (
    <AuthContext.Provider value={value}>
      {children}
      {value.mustChangePassword && <ForcePasswordChangeModal />}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

export function useAuthOptional(): AuthContextValue | null {
  return useContext(AuthContext) ?? null;
}

/**
 * Normalize an `IdentityContext` from any of /login, /refresh-identity, /me.
 * Backend uses the legacy `loging_*` spellings for the logging role; UI keeps
 * the modern `logging_*`. Bridge them here, in one place.
 */
function normalizeMe(me: MeResponse): IdentityContext {
  const platformRole = me.platform_role ?? null;
  const mapped =
    platformRole === "loging_admin"
      ? "logging_admin"
      : platformRole === "loging_reader"
        ? "logging_reader"
        : platformRole === "department_admin"
          ? "dep_admin"
          : platformRole;
  return { ...me, platform_role: mapped };
}
