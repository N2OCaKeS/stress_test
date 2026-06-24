import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ToastProvider } from "@/contexts/ToastContext";
import type { Persona } from "@/types/persona";
import type { ConsoleMacro } from "@/api/server/consoleMacros";

const listMock = vi.fn();
const createMock = vi.fn();
const updateMock = vi.fn();
const deleteMock = vi.fn();
vi.mock("@/api/server/consoleMacros", () => ({
  listConsoleMacros: () => listMock(),
  createConsoleMacro: (b: unknown) => createMock(b),
  updateConsoleMacro: (id: string, b: unknown) => updateMock(id, b),
  deleteConsoleMacro: (id: string) => deleteMock(id),
}));

let currentPersona: Persona;
vi.mock("@/contexts/PersonaContext", () => ({
  usePersona: () => ({
    persona: currentPersona,
    setPersona: () => {},
    allPersonas: [],
  }),
}));

import { ConsoleMacrosPanel } from "@/pages/server/tabs/ConsoleMacros";

function persona(overrides: Partial<Persona>): Persona {
  return {
    id: "p",
    username: "p",
    email: "p@dbos.local",
    initials: "P",
    display_name: "p",
    dept_id: "core",
    platform_role: null,
    service_roles: {},
    accessible_services: ["server"],
    has_admin: false,
    tagline: "",
    ...overrides,
  } as Persona;
}

function macro(overrides: Partial<ConsoleMacro>): ConsoleMacro {
  return {
    id: "m1",
    name: "df",
    command_text: "df -h",
    display_order: 0,
    is_system: false,
    user_id: "p",
    department_id: "core",
    ...overrides,
  };
}

function renderPanel(onRun = vi.fn()) {
  render(
    <ToastProvider>
      <ConsoleMacrosPanel onRun={onRun} />
    </ToastProvider>,
  );
  return onRun;
}

describe("ConsoleMacrosPanel", () => {
  beforeEach(() => {
    listMock.mockReset();
    createMock.mockReset();
    updateMock.mockReset();
    deleteMock.mockReset();
  });

  it("рендерит группы «Мои» и «Системные»", async () => {
    currentPersona = persona({});
    listMock.mockResolvedValue([
      macro({ id: "m1", name: "personal-1", is_system: false }),
      macro({ id: "m2", name: "system-1", is_system: true, user_id: null }),
    ]);

    renderPanel();

    expect(await screen.findByText("Мои")).toBeInTheDocument();
    expect(screen.getByText("Системные")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "personal-1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "system-1" })).toBeInTheDocument();
  });

  it("клик по макросу шлёт command + \\n через onRun", async () => {
    currentPersona = persona({});
    listMock.mockResolvedValue([
      macro({ id: "m1", name: "uptime", command_text: "uptime" }),
    ]);
    const onRun = renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: "uptime" }));
    expect(onRun).toHaveBeenCalledWith("uptime");
  });

  it("создание личного макроса вызывает createConsoleMacro", async () => {
    currentPersona = persona({});
    listMock.mockResolvedValue([]);
    createMock.mockResolvedValue(macro({ id: "new" }));

    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: /Новый/ }));
    fireEvent.change(screen.getByPlaceholderText("например, df -h"), {
      target: { value: "free -m" },
    });
    fireEvent.change(
      screen.getByPlaceholderText("команда, которая уйдёт в терминал"),
      { target: { value: "free -m" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Создать" }));

    await waitFor(() =>
      expect(createMock).toHaveBeenCalledWith(
        expect.objectContaining({ name: "free -m", command_text: "free -m" }),
      ),
    );
    // Личный макрос — без is_system.
    expect(createMock.mock.calls[0][0].is_system).toBeUndefined();
  });

  it("не-dep_admin не видит переключатель «системный» и не правит системные", async () => {
    currentPersona = persona({ service_roles: { server: "operator" } });
    listMock.mockResolvedValue([
      macro({ id: "m2", name: "system-1", is_system: true, user_id: null }),
    ]);

    renderPanel();

    // Системный макрос виден кнопкой, но без кнопок правки/удаления.
    await screen.findByRole("button", { name: "system-1" });
    expect(
      screen.queryByRole("button", { name: /Изменить system-1/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Удалить system-1/ }),
    ).not.toBeInTheDocument();

    // В редакторе создания переключатель «системный» недоступен.
    fireEvent.click(screen.getByRole("button", { name: /Новый/ }));
    expect(screen.queryByText(/Системный \(виден/)).not.toBeInTheDocument();
  });

  it("dep_admin видит переключатель «системный»", async () => {
    currentPersona = persona({ platform_role: "dep_admin" });
    listMock.mockResolvedValue([]);

    renderPanel();

    fireEvent.click(await screen.findByRole("button", { name: /Новый/ }));
    expect(screen.getByText(/Системный \(виден/)).toBeInTheDocument();
  });
});
