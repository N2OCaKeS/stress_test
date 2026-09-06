import { lazy, Suspense, useEffect } from "react";
import {
  BrowserRouter,
  Routes,
  Route,
  Navigate,
  useLocation,
} from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { AuthProvider, USE_MOCK_AUTH } from "@/contexts/AuthContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { ConfirmProvider } from "@/components/ui/ConfirmDialog";
import { LabelsProvider } from "@/lib/labels";
import { getPublicPasswordPolicy } from "@/api/auth/passwordPolicy";
import { setActivePasswordPolicy } from "@/lib/passwordPolicy";
import { PersonaSelector } from "@/pages/auth/PersonaSelector";
import { Login } from "@/pages/system/Login";
import { NotFound } from "@/pages/system/NotFound";
import { RouteGuard } from "@/components/RouteGuard";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { ReencryptBanner } from "@/components/ReencryptBanner";

const Home = lazy(() =>
  import("@/pages/home/Home").then((m) => ({ default: m.Home }))
);
const Server = lazy(() =>
  import("@/pages/server/Server").then((m) => ({ default: m.Server }))
);
const ServerUsers = lazy(() =>
  import("@/pages/server/ServerUsers").then((m) => ({ default: m.ServerUsers }))
);
const ServerPackages = lazy(() =>
  import("@/pages/server/ServerPackages").then((m) => ({
    default: m.ServerPackages,
  }))
);
const Secret = lazy(() =>
  import("@/pages/secret/Secret").then((m) => ({ default: m.Secret }))
);
const Vm = lazy(() =>
  import("@/pages/vm/Vm").then((m) => ({ default: m.Vm }))
);
const Users = lazy(() =>
  import("@/pages/users/Users").then((m) => ({ default: m.Users }))
);
const UserDetail = lazy(() =>
  import("@/pages/users/UserDetail").then((m) => ({ default: m.UserDetail }))
);
const GroupDetail = lazy(() =>
  import("@/pages/users/GroupDetail").then((m) => ({ default: m.GroupDetail }))
);
const BotDetail = lazy(() =>
  import("@/pages/users/BotDetail").then((m) => ({ default: m.BotDetail }))
);
const MyAccount = lazy(() =>
  import("@/pages/users/MyAccount").then((m) => ({ default: m.MyAccount }))
);
const Admin = lazy(() =>
  import("@/pages/admin/Admin").then((m) => ({ default: m.Admin }))
);
const Log = lazy(() =>
  import("@/pages/log/Log").then((m) => ({ default: m.Log }))
);
const LogRules = lazy(() =>
  import("@/pages/log/LogRules").then((m) => ({ default: m.LogRules }))
);
const LogRetention = lazy(() =>
  import("@/pages/log/LogRetention").then((m) => ({ default: m.LogRetention }))
);
const Worker = lazy(() =>
  import("@/pages/worker/Worker").then((m) => ({ default: m.Worker }))
);
const WorkerDlq = lazy(() =>
  import("@/pages/worker/WorkerDlq").then((m) => ({ default: m.WorkerDlq }))
);
const Patterns = lazy(() =>
  import("@/pages/patterns/Patterns").then((m) => ({ default: m.Patterns }))
);
const WikiExamples = lazy(() =>
  import("@/pages/wiki/WikiExamples").then((m) => ({ default: m.WikiExamples }))
);
const OsCatalog = lazy(() =>
  import("@/pages/os/OsCatalog").then((m) => ({ default: m.OsCatalog }))
);
const BoxCatalog = lazy(() =>
  import("@/pages/box/BoxCatalog").then((m) => ({ default: m.BoxCatalog }))
);
const TaskResultPage = lazy(() =>
  import("@/pages/tasks/TaskResultPage").then((m) => ({
    default: m.TaskResultPage,
  }))
);
const Testing = lazy(() =>
  import("@/pages/testing/Testing").then((m) => ({ default: m.Testing }))
);
const ServicesHealth = lazy(() =>
  import("@/pages/health/ServicesHealth").then((m) => ({
    default: m.ServicesHealth,
  }))
);

// Редирект, сохраняющий query-строку (legacy /worker?server_id=… → новый
// раздел задач под «Серверами»). Navigate сам по себе query не переносит.
function RedirectPreservingSearch({ to }: { to: string }) {
  const { search } = useLocation();
  return <Navigate to={`${to}${search}`} replace />;
}

function RouteFallback() {
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

export function App() {
  // Подтягиваем актуальную парольную политику логина для клиентских
  // валидаторов (публичный эндпоинт, best-effort). В mock-режиме backend'а нет.
  useEffect(() => {
    if (USE_MOCK_AUTH) return;
    getPublicPasswordPolicy()
      .then(setActivePasswordPolicy)
      .catch(() => {});
  }, []);
  return (
    <ThemeProvider>
      <BrowserRouter>
        <AuthProvider>
          <PersonaProvider>
            <ToastProvider>
              <LabelsProvider>
              <ConfirmProvider>
              <ReencryptBanner />
              <ErrorBoundary>
              <Suspense fallback={<RouteFallback />}>
              <Routes>
            <Route
              path="/"
              element={
                USE_MOCK_AUTH ? <PersonaSelector /> : <Navigate to="/login" replace />
              }
            />
            <Route path="/login" element={<Login />} />
            <Route
              path="/home"
              element={
                <RouteGuard>
                  <Home />
                </RouteGuard>
              }
            />
            <Route
              path="/secret"
              element={
                <RouteGuard service="secret">
                  <Secret />
                </RouteGuard>
              }
            />
            <Route
              path="/server"
              element={
                <RouteGuard service="server">
                  <Server />
                </RouteGuard>
              }
            />
            <Route
              path="/servers"
              element={
                <RouteGuard service="server">
                  <Server />
                </RouteGuard>
              }
            />
            {/* IPMI-флот как отдельный раздел убран — IPMI живёт во вкладке
                карточки сервера. Старый маршрут редиректим на список серверов;
                IpmiFleet больше не монтируется. */}
            <Route path="/server/ipmi" element={<Navigate to="/server" replace />} />
            <Route
              path="/vm"
              element={
                <RouteGuard service="server">
                  <Vm />
                </RouteGuard>
              }
            />
            <Route
              path="/server/users"
              element={
                <RouteGuard service="server">
                  <ServerUsers />
                </RouteGuard>
              }
            />
            <Route
              path="/server/packages"
              element={
                <RouteGuard service="server">
                  <ServerPackages />
                </RouteGuard>
              }
            />
            <Route
              path="/server/tasks"
              element={
                <RouteGuard service="server">
                  <Worker />
                </RouteGuard>
              }
            />
            <Route
              path="/server/tasks/dlq"
              element={
                <RouteGuard service="server">
                  <WorkerDlq />
                </RouteGuard>
              }
            />
            <Route
              path="/tasks/:id"
              element={
                <RouteGuard service="server">
                  <TaskResultPage />
                </RouteGuard>
              }
            />
            <Route
              path="/testing"
              element={
                <RouteGuard service="server">
                  <Testing />
                </RouteGuard>
              }
            />
            <Route
              path="/testing/:section"
              element={
                <RouteGuard service="server">
                  <Testing />
                </RouteGuard>
              }
            />
            <Route
              path="/health"
              element={
                <RouteGuard>
                  <ServicesHealth />
                </RouteGuard>
              }
            />
            <Route
              path="/users"
              element={
                <RouteGuard service="auth">
                  <Users />
                </RouteGuard>
              }
            />
            <Route
              path="/users/group/:id"
              element={
                <RouteGuard service="auth">
                  <GroupDetail />
                </RouteGuard>
              }
            />
            <Route
              path="/users/bot/:id"
              element={
                <RouteGuard service="auth">
                  <BotDetail />
                </RouteGuard>
              }
            />
            <Route
              path="/users/:id"
              element={
                <RouteGuard service="auth">
                  <UserDetail />
                </RouteGuard>
              }
            />
            <Route
              path="/log"
              element={
                <RouteGuard service="logging">
                  <Log />
                </RouteGuard>
              }
            />
            <Route
              path="/log/rules"
              element={
                <RouteGuard service="logging" logMutation>
                  <LogRules />
                </RouteGuard>
              }
            />
            <Route
              path="/log/retention"
              element={
                <RouteGuard service="logging" logMutation>
                  <LogRetention />
                </RouteGuard>
              }
            />
            {/* Задачи переехали под «Серверы» — /worker* редиректим на
                /server/tasks*, сохраняя query (например ?server_id=… из
                карточки сервера). */}
            <Route path="/worker" element={<RedirectPreservingSearch to="/server/tasks" />} />
            <Route
              path="/worker/dlq"
              element={<RedirectPreservingSearch to="/server/tasks/dlq" />}
            />
            <Route path="/patterns" element={<Patterns />} />
            <Route
              path="/wiki"
              element={
                <RouteGuard>
                  <WikiExamples />
                </RouteGuard>
              }
            />
            <Route
              path="/os"
              element={
                <RouteGuard>
                  <OsCatalog />
                </RouteGuard>
              }
            />
            <Route
              path="/boxes"
              element={
                <RouteGuard service="server">
                  <BoxCatalog />
                </RouteGuard>
              }
            />
            <Route path="/security" element={<Navigate to="/admin" replace />} />
            <Route
              path="/security/tokens"
              element={<Navigate to="/admin/services.security.tokens" replace />}
            />
            <Route
              path="/security/oauth2"
              element={<Navigate to="/admin/services.security.oauth2" replace />}
            />
            <Route
              path="/security/docker"
              element={<Navigate to="/admin/services.security.docker" replace />}
            />
            <Route
              path="/security/lockout"
              element={<Navigate to="/admin/services.security.lockout" replace />}
            />
            <Route
              path="/security/introspect"
              element={<Navigate to="/admin/services.security.introspect" replace />}
            />
            <Route
              path="/security/service-access"
              element={<Navigate to="/admin/services.security.service_access" replace />}
            />
            <Route
              path="/security/services"
              element={<Navigate to="/admin/services.security.services" replace />}
            />
            <Route
              path="/admin"
              element={
                <RouteGuard requireAdmin>
                  <Admin />
                </RouteGuard>
              }
            />
            <Route
              path="/admin/:itemId"
              element={
                <RouteGuard requireAdmin>
                  <Admin />
                </RouteGuard>
              }
            />
            <Route path="/settings" element={<Navigate to="/me" replace />} />
            <Route
              path="/me"
              element={
                <RouteGuard>
                  <MyAccount />
                </RouteGuard>
              }
            />
            <Route path="/404" element={<NotFound />} />
            <Route path="*" element={<Navigate to="/404" replace />} />
              </Routes>
              </Suspense>
              </ErrorBoundary>
              </ConfirmProvider>
              </LabelsProvider>
            </ToastProvider>
          </PersonaProvider>
        </AuthProvider>
      </BrowserRouter>
    </ThemeProvider>
  );
}
