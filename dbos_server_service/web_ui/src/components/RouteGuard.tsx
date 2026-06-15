import { useEffect, useRef } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { usePersona } from "@/contexts/PersonaContext";
import { useToast } from "@/contexts/ToastContext";
import { USE_MOCK_AUTH, useAuthOptional } from "@/contexts/AuthContext";
import {
  hasAuditLogAccess,
  hasAuditMutateAccess,
  hasSecretZoneAccess,
  hasServerZoneAccess,
} from "@/lib/rbac";
import type { ServiceName } from "@/types/persona";

interface Props {
  /**
   * Required service access. Optional — pass `undefined` for routes gated
   * purely by `requireAdmin` that span multiple services.
   */
  service?: ServiceName;
  requireAdmin?: boolean; // require has_admin too
  /**
   * Логовый mutation-роут (`/log/rules`, `/log/retention`). Гейтится строже
   * read-канала: только `logging_admin`. `account_admin` / `logging_reader`
   * читают журнал, но на правила/retention backend ответит 403 — не пускаем
   * их даже открыть страницу.
   */
  logMutation?: boolean;
  children: React.ReactNode;
}

// reads accessible_services from PersonaContext; backed by auth_service /me
export function RouteGuard({ service, requireAdmin, logMutation, children }: Props) {
  const auth = useAuthOptional();
  const { persona } = usePersona();
  const location = useLocation();
  const toast = useToast();
  const notifiedRef = useRef(false);

  const bootstrapPending = auth?.isLoading === true;
  // Real-mode unauthenticated: redirect to login. Mock mode without provider
  // (test harness) falls through to persona-based check.
  const unauthenticated =
    !bootstrapPending &&
    !USE_MOCK_AUTH &&
    (!auth || !auth.user);

  // /log* (service === "logging") гейтится не по accessible_services, а по
  // platform-роли: backend loging_service пускает к событиям/правилам/retention
  // только loging_admin/loging_reader. Сервис-роль loging_service.admin (её
  // несёт dep_admin) кладёт `logging` в accessible_services, но backend всё
  // равно вернёт 403 — поэтому такой персоне раздел недоступен.
  // Зонные сервисы гейтим теми же хелперами, что и nav-чипы, чтобы у роли без
  // реального доступа раздел не просто упирался в BlockedPane, а вообще не
  // открывался (redirect на /home). logging — по platform-роли; server/worker
  // и secret — по zone-access (отсекает business-data-denied платформенные роли
  // вроде account_admin, которым backend всё равно ответит 403).
  const hasService = !service
    ? true
    : service === "logging"
      ? logMutation
        ? hasAuditMutateAccess(persona)
        : hasAuditLogAccess(persona)
      : service === "secret"
        ? hasSecretZoneAccess(persona)
        : service === "server" || service === "worker"
          ? hasServerZoneAccess(persona)
          : persona.accessible_services.includes(service);
  const adminOk = !requireAdmin || persona.has_admin;
  const denied = !bootstrapPending && !unauthenticated && (!hasService || !adminOk);

  useEffect(() => {
    if (denied && !notifiedRef.current) {
      notifiedRef.current = true;
      const reason = !hasService
        ? `Нет доступа к ${service}`
        : `Нужны admin-права${service ? ` для ${service}` : ""}`;
      toast.warn(reason);
    }
  }, [denied, hasService, service, toast]);

  // Bootstrap phase: AuthContext is still resolving /me via refresh token.
  // Render a splash so the user cannot slip through on the pre-auth fallback
  // persona while we wait for the real identity.
  if (bootstrapPending) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100vh",
        }}
      >
        <div className="spinner big" />
      </div>
    );
  }

  if (unauthenticated) {
    return (
      <Navigate
        to="/login"
        state={{ from: location.pathname }}
        replace
      />
    );
  }

  if (denied) {
    return (
      <Navigate
        to="/home"
        state={{ from: location.pathname, reason: "no-access", service }}
        replace
      />
    );
  }
  return <>{children}</>;
}
