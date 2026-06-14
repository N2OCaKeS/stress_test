import { lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { AuthProvider, USE_MOCK_AUTH } from "@/contexts/AuthContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { LabelsProvider } from "@/lib/labels";
import { PersonaSelector } from "@/pages/auth/PersonaSelector";
import { Login } from "@/pages/system/Login";
import { NotFound } from "@/pages/system/NotFound";
import { RouteGuard } from "@/components/RouteGuard";
import { ErrorBoundary } from "@/components/ErrorBoundary";

const Home = lazy(() =>
  import("@/pages/home/Home").then((m) => ({ default: m.Home }))
);
const Server = lazy(() =>
  import("@/pages/server/Server").then((m) => ({ default: m.Server }))
);
const IpmiFleet = lazy(() =>
  import("@/pages/server/IpmiFleet").then((m) => ({ default: m.IpmiFleet }))
);
const Secret = lazy(() =>
  import("@/pages/secret/Secret").then((m) => ({ default: m.Secret }))
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
const WizardCreateCredential = lazy(() =>
  import("@/pages/system/WizardCreateCredential").then((m) => ({
    default: m.WizardCreateCredential,
  }))
);
const WizardRotation = lazy(() =>
  import("@/pages/system/WizardRotation").then((m) => ({
    default: m.WizardRotation,
  }))
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
  return (
    <ThemeProvider>
      <BrowserRouter>
        <AuthProvider>
          <PersonaProvider>
            <ToastProvider>
              <LabelsProvider>
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
            <Route
              path="/server/ipmi"
              element={
                <RouteGuard service="server">
                  <IpmiFleet />
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
                <RouteGuard service="logging">
                  <LogRules />
                </RouteGuard>
              }
            />
            <Route
              path="/log/retention"
              element={
                <RouteGuard service="logging">
                  <LogRetention />
                </RouteGuard>
              }
            />
            <Route
              path="/worker"
              element={
                <RouteGuard service="worker">
                  <Worker />
                </RouteGuard>
              }
            />
            <Route
              path="/worker/dlq"
              element={
                <RouteGuard service="worker">
                  <WorkerDlq />
                </RouteGuard>
              }
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
            <Route
              path="/wizard/create-credential"
              element={
                <RouteGuard service="secret">
                  <WizardCreateCredential />
                </RouteGuard>
              }
            />
            <Route
              path="/wizard/rotation"
              element={
                <RouteGuard service="secret">
                  <WizardRotation />
                </RouteGuard>
              }
            />
            <Route path="/404" element={<NotFound />} />
            <Route path="*" element={<Navigate to="/404" replace />} />
              </Routes>
              </Suspense>
              </ErrorBoundary>
              </LabelsProvider>
            </ToastProvider>
          </PersonaProvider>
        </AuthProvider>
      </BrowserRouter>
    </ThemeProvider>
  );
}
