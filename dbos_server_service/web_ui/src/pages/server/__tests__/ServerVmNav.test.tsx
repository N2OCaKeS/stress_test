import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { Server } from "@/pages/server/Server";

// VmDetail тянет useConfirm; в этом тесте деструктив не дёргаем — no-op хватает,
// чтобы не тащить Radix-портал в jsdom.
vi.mock("@/components/ui/ConfirmDialog", () => ({
  useConfirm: () => ({
    confirm: vi.fn(async () => false),
    prompt: vi.fn(async () => ({ ok: false, reason: "" })),
    alert: vi.fn(async () => {}),
  }),
  ConfirmProvider: ({ children }: { children: unknown }) => children,
}));

// listServers держим в пендинге — тест про ветку ВМ, серверный список не нужен.
vi.mock("@/api/server/servers", () => ({
  listServers: vi.fn(() => new Promise(() => {})),
  getServer: vi.fn(() => new Promise(() => {})),
  createServer: vi.fn(),
  deleteServer: vi.fn(),
  updateServer: vi.fn(),
}));

vi.mock("@/api/auth/departments", () => ({
  listDepartments: vi.fn(() => new Promise(() => {})),
}));

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

function renderServer(entry = "/server?only=vms") {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <MemoryRouter initialEntries={[entry]}>
            <Server />
            <LocationProbe />
          </MemoryRouter>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

// Строка ВМ в среднем списке — общий EntityRow (div.cred-row с кнопкой внутри),
// такой же, как у сервера; карточка ВМ справа рисует то же имя в <h1>. Достаём
// именно кликабельную кнопку строки списка.
function vmListRow(name: string): HTMLElement {
  const hit = screen
    .getAllByText(name)
    .map((el) => el.closest(".cred-row")?.querySelector("button") ?? null)
    .find((btn): btn is HTMLButtonElement => btn !== null);
  if (!hit) throw new Error(`Строка ВМ «${name}» в списке не найдена`);
  return hit;
}

describe("Server list → открытие ВМ сохраняет средний список ВМ", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("клик по ВМ рисует VmDetail справа, а средняя панель остаётся списком ВМ", async () => {
    renderServer("/server?only=vms");

    // Средняя панель — список ВМ (группа + строка). Серверов в режиме only=vms нет.
    expect(await screen.findByText(/Виртуальные машины ·/)).toBeInTheDocument();
    expect(await screen.findByText("alse-1.8-rc")).toBeInTheDocument();
    expect(screen.queryByText(/Серверы · /)).not.toBeInTheDocument();
    // До выбора справа — пустой стейт, карточки ВМ ещё нет.
    expect(screen.queryByRole("heading", { name: /Параметры/ })).toBeNull();

    fireEvent.click(vmListRow("alse-1.8-rc"));

    // Справа появилась карточка ВМ (VmDetail): шапка «К хабу» + блок «Параметры».
    expect(
      await screen.findByRole("heading", { name: /Параметры/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /К хабу/ })).toBeInTheDocument();

    // Средняя панель НЕ подменилась: список ВМ на месте, хаб-центричной раскладки
    // (/vm со списком «VMS-hub») и карточки сервера нет.
    expect(screen.getByText(/Виртуальные машины ·/)).toBeInTheDocument();
    expect(vmListRow("alse-1.8-rc")).toBeInTheDocument();
    expect(screen.queryByText(/VMS-hub ·/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Выберите сервер слева для просмотра деталей/),
    ).toBeNull();
  });

  it("выбранная ВМ подсвечена в списке, фильтр only=vms не теряется", async () => {
    renderServer("/server?only=vms");
    await screen.findByText("alse-1.8-rc");

    fireEvent.click(vmListRow("alse-1.8-rc"));
    await screen.findByRole("heading", { name: /Параметры/ });

    // Активная строка получает класс active (на обёртке .cred-row).
    expect(
      vmListRow("alse-1.8-rc").closest(".cred-row")!.className,
    ).toContain("active");

    // URL сохранил only=vms и добавил vm=<id>, не ушёл на /vm.
    const loc = screen.getByTestId("loc").textContent ?? "";
    expect(loc.startsWith("/server")).toBe(true);
    expect(loc).toContain("only=vms");
    expect(loc).toContain("vm=vm-101");
  });

  it("«К хабу» закрывает карточку, но список ВМ и only=vms остаются", async () => {
    renderServer("/server?only=vms");
    await screen.findByText("alse-1.8-rc");
    fireEvent.click(vmListRow("alse-1.8-rc"));

    const back = await screen.findByRole("button", { name: /К хабу/ });
    fireEvent.click(back);

    // Карточка ушла, средний список ВМ остался, фильтр не потерян.
    expect(screen.queryByRole("heading", { name: /Параметры/ })).toBeNull();
    expect(screen.getByText(/Виртуальные машины ·/)).toBeInTheDocument();
    const loc = screen.getByTestId("loc").textContent ?? "";
    expect(loc).toContain("only=vms");
    expect(loc).not.toContain("vm=");
  });
});
