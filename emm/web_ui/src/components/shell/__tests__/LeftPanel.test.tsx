import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { Persona, ServiceName } from "@/types/persona";
import type { NavLinkItem } from "@/api/auth/navLinks";

/**
 * Аудит-чип на левой панели гейтится read-доступом к журналу
 * (`hasAuditLogAccess`), а не подключением отдела к loging_service. У dep_admin
 * его отдел к логированию обычно НЕ подключён, поэтому `logging` не попадает в
 * accessible_services — но раздел ему доступен. Проверяем, что чип «Журнал аудита»
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

vi.mock("@/api/health", () => ({
  checkAllServices: () => new Promise(() => {}),
}));

// Индикатор пересчёта статистики (§9.3 плана миграции testing_service) —
// подменяемый резолвер статуса, управляемый из теста.
let currentStatisticsStatus: import("@/api/testing/types").StatisticsRecalcStatus | null = null;
vi.mock("@/api/testing/statistics", () => ({
  getStatisticsStatus: () =>
    currentStatisticsStatus
      ? Promise.resolve(currentStatisticsStatus)
      : new Promise(() => {}),
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
  currentStatisticsStatus = null;
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

describe("LeftPanel — server-подменю", () => {
  it("не содержит пункт «Виртуализация» (переехал в карточку сервера)", async () => {
    renderPanel(
      makePersona({
        username: "regular",
        service_roles: { server: "operator" },
        accessible_services: ["server"] as ServiceName[],
      }),
    );
    // Подменю «Серверы» отрисовалось.
    expect(await screen.findByText("Пакеты")).toBeInTheDocument();
    // Пункта «Виртуализация» и ссылки на /vm в панели больше нет.
    expect(screen.queryByText("Виртуализация")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /Виртуализация/ }),
    ).not.toBeInTheDocument();
  });
});

describe("LeftPanel — имя текущего пользователя", () => {
  it("показывает ФИО, когда оно есть", async () => {
    renderPanel(
      makePersona({
        username: "ivanov",
        last_name: "Иванов",
        first_name: "Иван",
        accessible_services: ["server"] as ServiceName[],
        service_roles: { server: "reader" },
      }),
    );
    await screen.findByText("ОС");
    expect(screen.getByText("Иванов Иван")).toBeInTheDocument();
  });

  it("падает на username, когда ФИО нет", async () => {
    renderPanel(
      makePersona({
        username: "ivanov",
        accessible_services: ["server"] as ServiceName[],
        service_roles: { server: "reader" },
      }),
    );
    await screen.findByText("ОС");
    expect(screen.getByText("ivanov")).toBeInTheDocument();
  });
});

describe("LeftPanel — аудит-чип", () => {
  it("dep_admin без logging в accessible_services видит «Журнал аудита»", async () => {
    renderPanel(
      makePersona({
        username: "dep_admin1",
        platform_role: "dep_admin",
        // Отдел подключён к server/secret, но НЕ к logging — как на проде.
        accessible_services: ["server", "secret"] as ServiceName[],
        has_admin: true,
      }),
    );
    expect(await screen.findByText("Журнал аудита")).toBeInTheDocument();
  });

  it("обычный dept-пользователь без audit-доступа не видит «Журнал аудита»", async () => {
    renderPanel(
      makePersona({
        username: "regular",
        platform_role: null,
        service_roles: { server: "reader" },
        accessible_services: ["server"] as ServiceName[],
      }),
    );
    await screen.findByText("ОС");
    expect(screen.queryByText("Журнал аудита")).not.toBeInTheDocument();
  });

  it("logging_admin с logging в accessible_services получает ровно один чип", async () => {
    renderPanel(
      makePersona({
        username: "log_admin",
        dept_id: null,
        platform_role: "logging_admin",
        accessible_services: ["logging", "config"] as ServiceName[],
        has_admin: true,
      }),
    );
    expect(await screen.findByText("Журнал аудита")).toBeInTheDocument();
    expect(screen.getAllByText("Журнал аудита")).toHaveLength(1);
  });
});

describe("LeftPanel — индикатор пересчёта статистики", () => {
  it("не рендерится, когда пересчёт ни разу не запускался (idle) — это индикатор, не постоянная плашка", async () => {
    currentStatisticsStatus = {
      status: "idle", triggered_by: null, test_run_id: null,
      started_at: null, finished_at: null, error: null, updated_at: null,
    };
    renderPanel(
      makePersona({
        username: "regular",
        service_roles: { server: "reader" },
        accessible_services: ["server"] as ServiceName[],
      }),
    );
    await screen.findByText("ОС");
    expect(screen.queryByText("Статистика")).not.toBeInTheDocument();
  });

  it("не рендерится после завершения пересчёта (succeeded/failed) — только пока фактически идёт", async () => {
    currentStatisticsStatus = {
      status: "succeeded", triggered_by: "manual", test_run_id: null,
      started_at: "2026-09-15T10:00:00Z", finished_at: "2026-09-15T10:05:00Z",
      error: null, updated_at: null,
    };
    renderPanel(
      makePersona({
        username: "regular",
        service_roles: { server: "reader" },
        accessible_services: ["server"] as ServiceName[],
      }),
    );
    await screen.findByText("ОС");
    expect(screen.queryByText("Статистика")).not.toBeInTheDocument();
  });

  it("показывает статус running с анимацией", async () => {
    currentStatisticsStatus = {
      status: "running", triggered_by: "test_run", test_run_id: "run_1",
      started_at: "2026-09-15T10:00:00Z", finished_at: null, error: null, updated_at: null,
    };
    renderPanel(
      makePersona({
        username: "regular",
        service_roles: { server: "reader" },
        accessible_services: ["server"] as ServiceName[],
      }),
    );
    expect(await screen.findByText("выполняется")).toBeInTheDocument();
  });

  it("не рендерится для персоны без доступа к server-зоне", async () => {
    currentStatisticsStatus = {
      status: "succeeded", triggered_by: "manual", test_run_id: null,
      started_at: "2026-09-15T10:00:00Z", finished_at: "2026-09-15T10:05:00Z",
      error: null, updated_at: null,
    };
    renderPanel(
      makePersona({
        username: "log_admin",
        dept_id: null,
        platform_role: "logging_admin",
        accessible_services: ["logging", "config"] as ServiceName[],
        has_admin: true,
      }),
    );
    await screen.findByText("ОС");
    expect(screen.queryByText("Статистика")).not.toBeInTheDocument();
  });
});
