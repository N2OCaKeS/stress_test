import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { AuthProvider, USE_MOCK_AUTH } from "@/contexts/AuthContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { LabelsProvider } from "@/lib/labels";
import { PersonaSelector } from "@/pages/auth/PersonaSelector";
import { Home } from "@/pages/home/Home";
import { Server } from "@/pages/server/Server";
import { Secret } from "@/pages/secret/Secret";
import { Users } from "@/pages/users/Users";
import { UserDetail } from "@/pages/users/UserDetail";
import { GroupDetail } from "@/pages/users/GroupDetail";
import { BotDetail } from "@/pages/users/BotDetail";
import { Admin } from "@/pages/admin/Admin";
import { Login } from "@/pages/system/Login";
import { NotFound } from "@/pages/system/NotFound";
import { MyAccount } from "@/pages/users/MyAccount";
import { WizardCreateCredential } from "@/pages/system/WizardCreateCredential";
import { WizardRotation } from "@/pages/system/WizardRotation";
import { Log } from "@/pages/log/Log";
import { LogRules } from "@/pages/log/LogRules";
import { LogRetention } from "@/pages/log/LogRetention";
import { Worker } from "@/pages/worker/Worker";
import { WorkerDlq } from "@/pages/worker/WorkerDlq";
import { Patterns } from "@/pages/patterns/Patterns";
import { RouteGuard } from "@/components/RouteGuard";
import { ErrorBoundary } from "@/components/ErrorBoundary";

export function App() {
  return (
    <ThemeProvider>
      <BrowserRouter>
        <AuthProvider>
          <PersonaProvider>
            <ToastProvider>
              <LabelsProvider>
              <ErrorBoundary>
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
              </ErrorBoundary>
              </LabelsProvider>
            </ToastProvider>
          </PersonaProvider>
        </AuthProvider>
      </BrowserRouter>
    </ThemeProvider>
  );
}
