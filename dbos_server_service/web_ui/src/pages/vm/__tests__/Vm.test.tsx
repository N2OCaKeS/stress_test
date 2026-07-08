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

  it("рендерит список хабов; подготовка/кандидаты переехали в карточку сервера", async () => {
    renderVm("/vm");
    // Хаб-заголовок и хаб из фикстур.
    expect(await screen.findByText(/VMS-hub · 2/)).toBeInTheDocument();
    expect(screen.getByText("kvm-hub-core-1")).toBeInTheDocument();
    // Секции «Кандидаты в hub» и кнопки prepare в /vm больше нет —
    // подготовка живёт в ServerDetail (вкладка «Управление»).
    expect(screen.queryByText(/Кандидаты в hub/)).not.toBeInTheDocument();
    expect(
      screen.queryByTitle(/Подготовить сервер как VMS-hub/),
    ).not.toBeInTheDocument();
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

  it("подготовленная ВМ показывает mgmt-учётку и кнопку ротации кред", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    expect(
      await screen.findByRole("heading", {
        name: /Подготовка и управляющие креды/,
      }),
    ).toBeInTheDocument();
    // mgmt-учётка из фикстуры и кнопка ротации.
    expect(await screen.findByText("dbosmgr")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Ротировать креды/ }),
    ).toBeInTheDocument();
  });

  it("неподготовленная ВМ показывает кнопку «Подготовить»", async () => {
    // vm-103 в фикстуре is_managed=false.
    renderVm("/vm?hub=srv-07&id=vm-103");
    expect(
      await screen.findByRole("button", { name: /Подготовить/ }),
    ).toBeInTheDocument();
  });

  it("карточка ВМ несёт раздел «Сеть» и открывает модалку смены сети", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    expect(await screen.findByRole("heading", { name: /^Сеть$/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Изменить сеть/ }));
    expect(await screen.findByText(/Сеть ВМ/)).toBeInTheDocument();
    // bridge по умолчанию — виден селектор пула IPAM.
    expect(screen.getByText(/Пул IPAM/)).toBeInTheDocument();
  });

  it("открывает раздел IP-пулов и рендерит список из фикстур", async () => {
    renderVm("/vm");
    fireEvent.click(await screen.findByRole("button", { name: /IP-пулы \(IPAM\)/ }));
    expect(
      await screen.findByRole("heading", { name: /IP-пулы \(IPAM\)/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("core-lan")).toBeInTheDocument();
    expect(screen.getByText("dtkk-lan")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Создать пул/ }),
    ).toBeInTheDocument();
  });

  it("открывает модалку создания IP-пула и валидирует поля", async () => {
    renderVm("/vm?zone=pools");
    const createBtn = await screen.findByRole("button", { name: /Создать пул/ });
    fireEvent.click(createBtn);
    expect(await screen.findByText("Новый IP-пул")).toBeInTheDocument();
    // После открытия модалки «Создать пул» есть и в шапке, и как сабмит формы —
    // берём сабмит (последний).
    const submitButtons = screen.getAllByRole("button", { name: /Создать пул/ });
    const submit = submitButtons[submitButtons.length - 1];
    // Пустая форма — сабмит disabled.
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText("core-lan"), {
      target: { value: "new-pool" },
    });
    fireEvent.change(screen.getByPlaceholderText("10.177.103.0/24"), {
      target: { value: "10.10.0.0/24" },
    });
    fireEvent.change(screen.getByPlaceholderText("10.177.103.1"), {
      target: { value: "10.10.0.1" },
    });
    fireEvent.change(screen.getByPlaceholderText("10.177.103.50"), {
      target: { value: "10.10.0.50" },
    });
    fireEvent.change(screen.getByPlaceholderText("10.177.103.99"), {
      target: { value: "10.10.0.99" },
    });
    fireEvent.change(screen.getByPlaceholderText("core"), {
      target: { value: "core" },
    });
    expect(submit).not.toBeDisabled();
  });

  it("модалка создания ВМ показывает выбор пула IPAM для bridge", async () => {
    renderVm("/vm?hub=srv-07&action=new");
    // bridge по умолчанию → селектор пула и радио выбора IP.
    expect(await screen.findByText(/Пул IPAM/)).toBeInTheDocument();
    expect(screen.getByText(/свободный автоматически/)).toBeInTheDocument();
    expect(screen.getByText(/выбрать из пула/)).toBeInTheDocument();
  });

  it("хаб показывает «Развернуть стандартные ВМ»; teardown переехал в карточку сервера", async () => {
    renderVm("/vm?hub=srv-24");
    expect(
      await screen.findByRole("button", { name: /Развернуть стандартные ВМ/ }),
    ).toBeInTheDocument();
    // Разбор хаба (teardown) больше не в /vm — он в ServerDetail.
    expect(
      screen.queryByRole("button", { name: /Разобрать VMS-hub/ }),
    ).not.toBeInTheDocument();
  });

  it("развёртывание стандартных ВМ требует подтверждения", async () => {
    renderVm("/vm?hub=srv-24");
    fireEvent.click(
      await screen.findByRole("button", { name: /Развернуть стандартные ВМ/ }),
    );
    // Открылся confirm-диалог.
    expect(
      await screen.findByText(/Развернуть на хабе/),
    ).toBeInTheDocument();
  });

  it("карточка ВМ несёт тумблер автозапуска и переключает его", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    // vm-101 в фикстуре autostart=true.
    const toggle = await screen.findByRole("switch", { name: /Автозапуск/ });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    fireEvent.click(toggle);
    // После клика (mock) — выключен.
    expect(
      await screen.findByRole("switch", { name: /Автозапуск/ }),
    ).toHaveAttribute("aria-checked", "false");
  });

  it("карточка ВМ несёт панель консоли с выбором SSH/VNC/serial", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    expect(await screen.findByRole("heading", { name: /Консоль/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^SSH$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^VNC$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Serial$/ })).toBeInTheDocument();
  });

  it("консоль SSH показывает команду подключения", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    await screen.findByRole("heading", { name: /Консоль/ });
    fireEvent.click(screen.getByRole("button", { name: /Открыть консоль/ }));
    // SSH по умолчанию — видна команда ssh.
    expect(await screen.findByText(/ssh dbosmgr@/)).toBeInTheDocument();
  });

  it("консоль VNC показывает ws-эндпоинт прокси (без внешнего вьювера)", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    await screen.findByRole("heading", { name: /Консоль/ });
    fireEvent.click(screen.getByRole("button", { name: /^VNC$/ }));
    fireEvent.click(screen.getByRole("button", { name: /Открыть консоль/ }));
    expect(
      (await screen.findAllByText(/wss:\/\/vms-console\.local/)).length,
    ).toBeGreaterThan(0);
  });

  it("открывает раздел «Пресеты ВМ» и рендерит список из фикстур", async () => {
    renderVm("/vm");
    fireEvent.click(await screen.findByRole("button", { name: /Пресеты ВМ/ }));
    expect(
      await screen.findByRole("heading", { name: /Пресеты стандартных ВМ/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("core-rc-bridge")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Создать пресет/ }),
    ).toBeInTheDocument();
  });

  it("открывает модалку создания пресета и валидирует поля", async () => {
    renderVm("/vm?zone=presets");
    fireEvent.click(await screen.findByRole("button", { name: /Создать пресет/ }));
    expect(await screen.findByText("Новый пресет ВМ")).toBeInTheDocument();
    const submits = screen.getAllByRole("button", { name: /Создать пресет/ });
    const submit = submits[submits.length - 1];
    // Имя есть по умолчанию пустое, но box и cpu заполнены — не хватает имени и отдела.
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText("core-rc-bridge"), {
      target: { value: "new-preset" },
    });
    fireEvent.change(screen.getByPlaceholderText("core"), {
      target: { value: "core" },
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
