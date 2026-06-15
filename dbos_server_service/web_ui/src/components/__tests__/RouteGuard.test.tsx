import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { RouteGuard } from "@/components/RouteGuard";
import type { ServiceName } from "@/types/persona";

/**
 * Прямой переход на guarded-роут под каждой mock-персоной. Проверяем, что
 * RouteGuard пускает/редиректит ровно так, как видит nav (LeftPanel рисует
 * сервис-чипы по accessible_services, кнопку «Администрирование» — по has_admin).
 * Любой direct-URL, расходящийся с этой видимостью, ловится здесь.
 */
function renderGuard(
  persona: string,
  opts: { service?: ServiceName; requireAdmin?: boolean },
) {
  window.localStorage.setItem("dbos-persona", persona);
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <MemoryRouter initialEntries={["/target"]}>
            <Routes>
              <Route
                path="/target"
                element={
                  <RouteGuard
                    service={opts.service}
                    requireAdmin={opts.requireAdmin}
                  >
                    <div>GRANTED</div>
                  </RouteGuard>
                }
              />
              <Route path="/home" element={<div>HOME-REDIRECT</div>} />
              <Route path="/login" element={<div>LOGIN-REDIRECT</div>} />
            </Routes>
          </MemoryRouter>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

function expectGranted() {
  expect(screen.getByText("GRANTED")).toBeInTheDocument();
}
function expectDenied() {
  expect(screen.getByText("HOME-REDIRECT")).toBeInTheDocument();
  expect(screen.queryByText("GRANTED")).not.toBeInTheDocument();
}

describe("RouteGuard — direct-URL RBAC per persona", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  // bob — account_admin: платформенный админ. Business-data-зоны (server /
  // worker / secret) и audit-журнал ему закрыты backend'ом, поэтому RouteGuard
  // теперь редиректит его на /home (раздел не открывается вместо BlockedPane).
  // Управление платформой — под /admin. auth и requireAdmin — granted.
  describe("account_admin (bob)", () => {
    it("secret → denied (зона скрыта, redirect вместо BlockedPane)", () => {
      renderGuard("bob", { service: "secret" });
      expectDenied();
    });
    it("server → denied (redirect, не BlockedPane)", () => {
      renderGuard("bob", { service: "server" });
      expectDenied();
    });
    it("auth → granted", () => {
      renderGuard("bob", { service: "auth" });
      expectGranted();
    });
    it("logging → denied (account_admin не читает audit, видит ClusterAuditOverview под /admin)", () => {
      renderGuard("bob", { service: "logging" });
      expectDenied();
    });
    it("worker → denied (redirect)", () => {
      renderGuard("bob", { service: "worker" });
      expectDenied();
    });
    it("requireAdmin → granted", () => {
      renderGuard("bob", { requireAdmin: true });
      expectGranted();
    });
  });

  // alice — dep_admin: всё кроме logging, плюс admin.
  describe("dep_admin (alice)", () => {
    it("secret → granted", () => {
      renderGuard("alice", { service: "secret" });
      expectGranted();
    });
    it("server → granted", () => {
      renderGuard("alice", { service: "server" });
      expectGranted();
    });
    it("auth → granted", () => {
      renderGuard("alice", { service: "auth" });
      expectGranted();
    });
    it("worker → granted", () => {
      renderGuard("alice", { service: "worker" });
      expectGranted();
    });
    it("logging → denied (нет в accessible_services)", () => {
      renderGuard("alice", { service: "logging" });
      expectDenied();
    });
    it("requireAdmin → granted", () => {
      renderGuard("alice", { requireAdmin: true });
      expectGranted();
    });
  });

  // carol — logging_admin: только logging (+config→/admin), плюс admin.
  describe("logging_admin (carol)", () => {
    it("logging → granted", () => {
      renderGuard("carol", { service: "logging" });
      expectGranted();
    });
    it("secret → denied", () => {
      renderGuard("carol", { service: "secret" });
      expectDenied();
    });
    it("server → denied", () => {
      renderGuard("carol", { service: "server" });
      expectDenied();
    });
    it("auth → denied", () => {
      renderGuard("carol", { service: "auth" });
      expectDenied();
    });
    it("worker → denied", () => {
      renderGuard("carol", { service: "worker" });
      expectDenied();
    });
    it("requireAdmin → granted", () => {
      renderGuard("carol", { requireAdmin: true });
      expectGranted();
    });
  });

  // dave — logging_reader: только logging, без admin.
  describe("logging_reader (dave)", () => {
    it("logging → granted", () => {
      renderGuard("dave", { service: "logging" });
      expectGranted();
    });
    it("secret → denied", () => {
      renderGuard("dave", { service: "secret" });
      expectDenied();
    });
    it("server → denied", () => {
      renderGuard("dave", { service: "server" });
      expectDenied();
    });
    it("requireAdmin → denied (has_admin=false)", () => {
      renderGuard("dave", { requireAdmin: true });
      expectDenied();
    });
  });
});
