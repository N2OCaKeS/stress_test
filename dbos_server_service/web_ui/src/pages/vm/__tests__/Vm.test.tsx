import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { ConfirmProvider } from "@/components/ui/ConfirmDialog";
import { Vm } from "@/pages/vm/Vm";

function renderVm(initialEntry: string) {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <ConfirmProvider>
            <MemoryRouter initialEntries={[initialEntry]}>
              <Vm />
            </MemoryRouter>
          </ConfirmProvider>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

describe("Vm zone (mock mode)", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });
  afterEach(() => {
    window.localStorage.clear();
  });

  it("рендерит список хабов и кандидатов на prepare для dep_admin (alice)", async () => {
    renderVm("/vm");
    // Хаб-заголовок и хаб из фикстур.
    expect(await screen.findByText(/VMS-hub · 2/)).toBeInTheDocument();
    expect(screen.getByText("kvm-hub-core-1")).toBeInTheDocument();
    // Кандидаты + кнопка prepare (Hub) видны носителю права.
    expect(screen.getByText(/Кандидаты в hub/)).toBeInTheDocument();
    expect(screen.getAllByTitle(/Подготовить сервер как VMS-hub/).length).toBeGreaterThan(0);
  });

  it("показывает ВМ хаба с индикатором питания при выборе хаба", async () => {
    renderVm("/vm?hub=srv-07");
    expect(await screen.findByText("alse-1.8-rc")).toBeInTheDocument();
    expect(screen.getByText("xfs-memleak")).toBeInTheDocument();
    // Кнопка создания ВМ доступна dep_admin'у.
    expect(screen.getByRole("button", { name: /Создать ВМ/ })).toBeInTheDocument();
  });

  it("карточка ВМ несёт кнопки питания и бронь", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    expect(await screen.findByRole("button", { name: /Start/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Shutdown/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Reboot/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Забронировать/ })).toBeInTheDocument();
  });

  it("открывает модалку создания ВМ и валидирует форму", async () => {
    renderVm("/vm?hub=srv-07&action=new");
    // Поле имени и кнопка сабмита.
    const submit = await screen.findByRole("button", { name: /Создать ВМ/ });
    // Пустое имя — кнопка disabled.
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText("alse-1.8-rc"), {
      target: { value: "test-vm" },
    });
    expect(submit).not.toBeDisabled();
  });

  it("блокирует зону для logging-роли (dave)", async () => {
    window.localStorage.setItem("dbos-persona", "dave");
    renderVm("/vm");
    expect(
      await screen.findByText(/Раздел недоступен для этой роли/),
    ).toBeInTheDocument();
  });
});
