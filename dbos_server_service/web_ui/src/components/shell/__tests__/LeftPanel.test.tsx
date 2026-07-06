import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { Persona, ServiceName } from "@/types/persona";
import type { NavLinkItem } from "@/api/auth/navLinks";

/**
 * Аудит-чип на левой панели гейтится read-доступом к журналу
 * (`hasAuditLogAccess`), а не подключением отдела к loging_service. У dep_admin
 * его отдел к логированию обычно НЕ подключён, поэтому `logging` не попадает в
 * accessible_services — но раздел ему доступен. Проверяем, что чип «Audit log»
 * появляется по доступу и не дублируется у платформенных logging-ролей.
 */

let currentPersona: Persona;
vi.mock("@/contexts/PersonaContext", () => ({
  usePersona: () => ({
    persona: currentPersona,
    setPersona: () => {},
    allPersonas: [],
  }),
}));

vi.mock("@/contexts/AuthContext", () => ({
  useAuthOptional: () => null,
}));

vi.mock("@/lib/labels", () => ({
  useDeptLabelOpt: () => null,
}));

// Настраиваемые кнопки панели ходят в backend через getNavLinks — подменяем
// на управляемый из теста массив, чтобы не тянуть fetch в jsdom.
let currentNavLinks: NavLinkItem[] = [];
vi.mock("@/api/auth/navLinks", () => ({
  getNavLinks: () => Promise.resolve(currentNavLinks),
}));

// Тяжёлые дочерние блоки панели тянут API/провайдеры — для теста навигации
// они не нужны, подменяем заглушками.
vi.mock("@/components/notifications/NotificationBell", () => ({
  NotificationBell: () => null,
}));
vi.mock("../ThemeSwitcher", () => ({
  ThemeSwitcher: () => null,
}));
vi.mock("../AdminOnlyPanel", () => ({
  AdminOnlyPanel: () => <div>admin-only-panel</div>,
}));

import { LeftPanel } from "@/components/shell/LeftPanel";

function makePersona(over: Partial<Persona>): Persona {
  return {
    id: "test",
    username: "tester",
    email: "tester@example.com",
    initials: "TT",
    display_name: "Tester",
    dept_id: "core",
    platform_role: null,
    service_roles: {},
    accessible_services: [],
    has_admin: false,
    tagline: "",
    ...over,
  };
}

function renderPanel(persona: Persona) {
  currentPersona = persona;
  return render(
    <MemoryRouter initialEntries={["/home"]}>
      <LeftPanel width={240} collapsed={false} onToggleCollapsed={() => {}} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  currentNavLinks = [];
});

describe("LeftPanel — настраиваемая кнопка allta", () => {
  it("рендерит кнопку, когда getNavLinks непустой", async () => {
    currentNavLinks = [{ label: "allta", url: "https://allta.example.ru/" }];
    renderPanel(
      makePersona({
        username: "regular",
        service_roles: { server: "reader" },
        accessible_services: ["server"] as ServiceName[],
      }),
    );
    const link = await screen.findByText("allta");
    const anchor = link.closest("a");
    expect(anchor).not.toBeNull();
    expect(anchor).toHaveAttribute("href", "https://allta.example.ru/");
    expect(anchor).toHaveAttribute("target", "_blank");
  });

  it("не рендерит кнопку, когда getNavLinks пуст", async () => {
    currentNavLinks = [];
    renderPanel(
      makePersona({
        username: "regular",
        service_roles: { server: "reader" },
        accessible_services: ["server"] as ServiceName[],
      }),
    );
    // Дожидаемся, пока панель отрисуется (по стабильному пункту «ОС»).
    await screen.findByText("ОС");
    expect(screen.queryByText("allta")).not.toBeInTheDocument();
  });
});

describe("LeftPanel — аудит-чип", () => {
  it("dep_admin без logging в accessible_services видит «Audit log»", () => {
    renderPanel(
      makePersona({
        username: "dep_admin1",
        platform_role: "dep_admin",
        // Отдел подключён к server/secret, но НЕ к logging — как на проде.
        accessible_services: ["server", "secret"] as ServiceName[],
        has_admin: true,
      }),
    );
    expect(screen.getByText("Audit log")).toBeInTheDocument();
  });

  it("обычный dept-пользователь без audit-доступа не видит «Audit log»", () => {
    renderPanel(
      makePersona({
        username: "regular",
        platform_role: null,
        service_roles: { server: "reader" },
        accessible_services: ["server"] as ServiceName[],
      }),
    );
    expect(screen.queryByText("Audit log")).not.toBeInTheDocument();
  });

  it("logging_admin с logging в accessible_services получает ровно один чип", () => {
    renderPanel(
      makePersona({
        username: "log_admin",
        dept_id: null,
        platform_role: "logging_admin",
        accessible_services: ["logging", "config"] as ServiceName[],
        has_admin: true,
      }),
    );
    expect(screen.getAllByText("Audit log")).toHaveLength(1);
  });
});
