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

  it("карточка ВМ рендерит раздел «Диски» со списком дисков", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    expect(await screen.findByRole("heading", { name: /Диски/ })).toBeInTheDocument();
    // Системный и доп. диск из фикстур.
    expect(await screen.findByText("system")).toBeInTheDocument();
    expect(screen.getByText("data")).toBeInTheDocument();
    // Системный диск не удаляется — кнопка удаления только у доп. диска.
    expect(screen.getAllByTitle("Удалить диск").length).toBe(1);
  });

  it("открывает модалку создания диска", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    fireEvent.click(await screen.findByRole("button", { name: /Создать диск/ }));
    expect(await screen.findByText("Новый диск")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("data")).toBeInTheDocument();
  });

  it("открывает модалку resize диска", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    const resizeBtns = await screen.findAllByRole("button", { name: /Resize/ });
    fireEvent.click(resizeBtns[0]);
    expect(await screen.findByText(/Resize диска/)).toBeInTheDocument();
  });

  it("открывает модалку изменения CPU/RAM", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    fireEvent.click(await screen.findByRole("button", { name: /Изменить CPU\/RAM/ }));
    expect(await screen.findByText(/Ресурсы ВМ/)).toBeInTheDocument();
  });

  it("модалка создания ВМ показывает каталог образов", async () => {
    renderVm("/vm?hub=srv-07&action=new");
    // Универсальный образ из каталога и его описание.
    expect(
      await screen.findByRole("option", { name: /vm_station · universal/ }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Universal-станция/)).toBeInTheDocument();
    expect(
      screen.getByTitle("Перечитать каталог образов с FTP"),
    ).toBeInTheDocument();
  });

  it("карточка ВМ рендерит раздел «Снимки» и прячет системные _build", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    expect(await screen.findByRole("heading", { name: /Снимки/ })).toBeInTheDocument();
    // Пользовательский снимок виден.
    expect(await screen.findByText("pre-regress")).toBeInTheDocument();
    // Системный `1.8.1.6_build` скрыт (is_system).
    expect(screen.queryByText("1.8.1.6_build")).not.toBeInTheDocument();
  });

  it("открывает модалку создания снимка", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    fireEvent.click(await screen.findByRole("button", { name: /Создать снимок/ }));
    expect(await screen.findByText("Новый снимок")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("pre-regress")).toBeInTheDocument();
  });

  it("текущий снимок не даёт откатить (кнопка disabled)", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    // У vm-101 текущий снимок — 1.8.1.6; у него кнопка «Откат» disabled.
    const revertBtns = await screen.findAllByRole("button", { name: /Откат/ });
    // Хотя бы одна активна (pre-regress) и хотя бы одна disabled (текущий).
    expect(revertBtns.some((b) => (b as HTMLButtonElement).disabled)).toBe(true);
    expect(revertBtns.some((b) => !(b as HTMLButtonElement).disabled)).toBe(true);
  });

  it("рендерит операции ОС/кред и открывает модалки astra-update и passwd", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    // Кнопки операций.
    expect(
      await screen.findByRole("button", { name: /Обновить ОС \(astra-update\)/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Обновить allta/ })).toBeInTheDocument();
    // astra-update модалка с выбором версии.
    fireEvent.click(screen.getByRole("button", { name: /Обновить ОС \(astra-update\)/ }));
    expect(await screen.findByText(/Обновление ОС ·/)).toBeInTheDocument();
    expect(
      await screen.findByRole("option", { name: "1.8.1.6" }),
    ).toBeInTheDocument();
  });

  it("открывает модалку смены пароля", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    fireEvent.click(await screen.findByRole("button", { name: /Обновить пароль/ }));
    expect(await screen.findByText(/Смена пароля ·/)).toBeInTheDocument();
  });

  it("рендерит селектор режима управляющих кред", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    expect(
      await screen.findByRole("heading", { name: /Режим управляющих кред/ }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("option", { name: /per_snapshot/ }),
    ).toBeInTheDocument();
  });

  it("блокирует зону для logging-роли (dave)", async () => {
    window.localStorage.setItem("dbos-persona", "dave");
    renderVm("/vm");
    expect(
      await screen.findByText(/Раздел недоступен для этой роли/),
    ).toBeInTheDocument();
  });
});
