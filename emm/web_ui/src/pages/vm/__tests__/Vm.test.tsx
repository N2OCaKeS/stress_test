import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, within, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { ConfirmProvider } from "@/components/ui/ConfirmDialog";
import { Vm } from "@/pages/vm/Vm";

// Вкладка «Консоль» ВМ монтирует тот же xterm-терминал, что серверная консоль;
// в jsdom подменяем его заглушкой (canvas/matchMedia недоступны).
vi.mock("@xterm/xterm", () => {
  class FakeTerminal {
    open() {}
    loadAddon() {}
    clear() {}
    write() {}
    writeln() {}
    focus() {}
    dispose() {}
    onData() {
      return { dispose() {} };
    }
  }
  return { Terminal: FakeTerminal };
});
vi.mock("@xterm/addon-fit", () => {
  class FakeFitAddon {
    fit() {}
  }
  return { FitAddon: FakeFitAddon };
});
vi.mock("@xterm/xterm/css/xterm.css", () => ({}));

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

/**
 * Карточка ВМ разложена по вкладкам (как у сервера): дождаться таб-бар и
 * переключиться на нужную вкладку по её подписи.
 */
async function openVmTab(label: string) {
  fireEvent.click(await screen.findByRole("button", { name: label }));
}

/** Dropdown-триггер, живущий в той же метке `<label>`, что и её подпись-текст. */
function dropdownTriggerNear(text: string | RegExp) {
  const label = screen.getByText(text).closest("label")!;
  return within(label).getByRole("button");
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

  it("вкладка «Питание» несёт кнопки питания; бронь живёт в шапке карточки", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    // Бронь вынесена в шапку карточки (как ReserveControl у сервера) — кнопка
    // видна независимо от активной вкладки.
    expect(
      await screen.findByRole("button", { name: /Забронировать/ }),
    ).toBeInTheDocument();
    await openVmTab("Питание");
    expect(await screen.findByRole("button", { name: /Start/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Shutdown/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Reboot/ })).toBeInTheDocument();
  });

  it("карточка ВМ рендерит таб-бар без вкладок «Сеть»/«Обслуживание»", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    for (const label of ["Обзор", "Питание", "Снимки", "Диски"]) {
      expect(
        await screen.findByRole("button", { name: label }),
      ).toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: "Сеть" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Обслуживание" }),
    ).not.toBeInTheDocument();
    // По умолчанию активна вкладка «Обзор» — видна секция «Идентификация».
    expect(
      await screen.findByRole("heading", { name: /Идентификация/ }),
    ).toBeInTheDocument();
    // Секции других вкладок пока не смонтированы.
    expect(
      screen.queryByRole("heading", { name: /^Снимки$/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: /^Диски$/ }),
    ).not.toBeInTheDocument();
  });

  it("открывает батч-форму создания ВМ и валидирует блок", async () => {
    renderVm("/vm?hub=srv-07&action=new");
    // Кнопка «Создать все» и «Добавить ВМ».
    const submit = await screen.findByRole("button", { name: /Создать все/ });
    expect(
      screen.getByRole("button", { name: /Добавить ВМ/ }),
    ).toBeInTheDocument();
    // Пустое имя — сабмит disabled.
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText("alse-1.8-rc"), {
      target: { value: "test-vm" },
    });
    expect(submit).not.toBeDisabled();
  });

  it("вкладка «Диски» рендерит список дисков", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    await openVmTab("Диски");
    expect(await screen.findByRole("heading", { name: /Диски/ })).toBeInTheDocument();
    // Системный и доп. диск из фикстур.
    expect(await screen.findByText("system")).toBeInTheDocument();
    expect(screen.getByText("data")).toBeInTheDocument();
    // Системный диск не удаляется — кнопка удаления только у доп. диска.
    expect(screen.getAllByTitle("Удалить диск").length).toBe(1);
  });

  it("открывает модалку создания диска", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    await openVmTab("Диски");
    fireEvent.click(await screen.findByRole("button", { name: /Создать диск/ }));
    expect(await screen.findByText("Новый диск")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("data")).toBeInTheDocument();
  });

  it("открывает модалку resize диска", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    await openVmTab("Диски");
    const resizeBtns = await screen.findAllByRole("button", { name: /Resize/ });
    fireEvent.click(resizeBtns[0]);
    expect(await screen.findByText(/Resize диска/)).toBeInTheDocument();
  });

  it("вкладка «Обзор» открывает модалку изменения CPU/RAM", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    fireEvent.click(await screen.findByRole("button", { name: /Изменить CPU\/RAM/ }));
    expect(await screen.findByText(/Ресурсы ВМ/)).toBeInTheDocument();
  });

  it("модалка создания ВМ показывает каталог образов", async () => {
    renderVm("/vm?hub=srv-07&action=new");
    // Заголовок «Образ (каталог) *» делит <label> с кнопкой «Обновить
    // каталог» и Dropdown-триггером — оба implicit-labeled этим заголовком,
    // поэтому различаем их по title (у кнопки обновления он есть).
    const imageLabelText = await screen.findByText("Образ (каталог) *");
    const imageLabel = imageLabelText.closest("label")!;
    const trigger = within(imageLabel)
      .getAllByRole("button")
      .find((b) => !b.hasAttribute("title"))!;
    fireEvent.click(trigger);
    expect(
      await screen.findByRole("option", { name: /vm_station · universal/ }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Universal-станция/)).toBeInTheDocument();
    expect(
      screen.getByTitle("Перечитать каталог образов с FTP"),
    ).toBeInTheDocument();
  });

  it("вкладка «Снимки» рендерит снимки и прячет системные _build", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    await openVmTab("Снимки");
    expect(await screen.findByRole("heading", { name: /Снимки/ })).toBeInTheDocument();
    // Пользовательский снимок виден.
    expect(await screen.findByText("pre-regress")).toBeInTheDocument();
    // Системный `1.8.1.6_build` скрыт (is_system).
    expect(screen.queryByText("1.8.1.6_build")).not.toBeInTheDocument();
  });

  it("вкладка «Снимки» разложена на 2 группы, обе со скроллом", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    await openVmTab("Снимки");
    expect(
      await screen.findByRole("heading", { name: "Версии ОС (чистые)" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Пользовательские" }),
    ).toBeInTheDocument();
    // Чистый снимок версии ОС и пользовательский — в разных группах.
    expect(await screen.findByText("1.8.1.6_орёл")).toBeInTheDocument();
    expect(screen.getByText("pre-regress")).toBeInTheDocument();
    // Оба списка — скроллируемые контейнеры.
    const baselineScroll = screen.getByTestId("snap-scroll-baseline");
    const userScroll = screen.getByTestId("snap-scroll-user");
    expect(baselineScroll.className).toMatch(/overflow-y-auto/);
    expect(userScroll.className).toMatch(/overflow-y-auto/);
  });

  it("поиск в группе версий ОС фильтрует снимки по имени", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    await openVmTab("Снимки");
    expect(await screen.findByText("1.8.1.6_орёл")).toBeInTheDocument();
    expect(screen.getByText("1.7.5.9_орёл")).toBeInTheDocument();
    fireEvent.change(
      screen.getByLabelText("Поиск снимков версий ОС"),
      { target: { value: "1.7" } },
    );
    expect(screen.getByText("1.7.5.9_орёл")).toBeInTheDocument();
    expect(screen.queryByText("1.8.1.6_орёл")).not.toBeInTheDocument();
  });

  it("поиск в пользовательской группе фильтрует снимки", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    await openVmTab("Снимки");
    expect(await screen.findByText("pre-regress")).toBeInTheDocument();
    expect(screen.getByText("hotfix-check")).toBeInTheDocument();
    fireEvent.change(
      screen.getByLabelText("Поиск пользовательских снимков"),
      { target: { value: "hotfix" } },
    );
    expect(screen.getByText("hotfix-check")).toBeInTheDocument();
    expect(screen.queryByText("pre-regress")).not.toBeInTheDocument();
  });

  it("открывает модалку создания снимка", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    await openVmTab("Снимки");
    fireEvent.click(await screen.findByRole("button", { name: /Создать снимок/ }));
    expect(await screen.findByText("Новый снимок")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("pre-regress")).toBeInTheDocument();
  });

  it("текущий снимок не даёт откатить (кнопка disabled)", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    await openVmTab("Снимки");
    // У vm-101 текущий снимок — 1.8.1.6; у него кнопка «Откат» disabled.
    const revertBtns = await screen.findAllByRole("button", { name: /Откат/ });
    // Хотя бы одна активна (pre-regress) и хотя бы одна disabled (текущий).
    expect(revertBtns.some((b) => (b as HTMLButtonElement).disabled)).toBe(true);
    expect(revertBtns.some((b) => !(b as HTMLButtonElement).disabled)).toBe(true);
  });

  it("вкладка «Снимки» несёт обновление ОС/allta и открывает astra-update", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    await openVmTab("Снимки");
    // Кнопки операций в карточке обновления ОС и allta.
    expect(
      await screen.findByRole("button", { name: /Обновить ОС \(astra-update\)/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Обновить allta/ })).toBeInTheDocument();
    // Смена пароля выпилена.
    expect(
      screen.queryByRole("button", { name: /Обновить пароль/ }),
    ).not.toBeInTheDocument();
    // astra-update модалка с выбором версии.
    fireEvent.click(screen.getByRole("button", { name: /Обновить ОС \(astra-update\)/ }));
    expect(await screen.findByText(/Обновление ОС ·/)).toBeInTheDocument();
    // Пока каталог версий грузится, вместо Dropdown — текст «Загрузка…»;
    // ждём, пока он не сменится на Dropdown-триггер.
    await waitFor(() => expect(dropdownTriggerNear("Версия ОС *")).toBeInTheDocument());
    fireEvent.click(dropdownTriggerNear("Версия ОС *"));
    expect(
      await screen.findByRole("option", { name: "1.8.1.6" }),
    ).toBeInTheDocument();
  });

  it("вкладка «Снимки» несёт селектор режима управляющих кред", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    await openVmTab("Снимки");
    expect(
      await screen.findByRole("heading", { name: /Режим управляющих кред/ }),
    ).toBeInTheDocument();
    fireEvent.click(dropdownTriggerNear("Режим"));
    expect(
      await screen.findByRole("option", { name: /per_snapshot/ }),
    ).toBeInTheDocument();
  });

  it("подготовленная ВМ показывает mgmt-учётку и кнопку ротации кред", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    await openVmTab("Управление");
    expect(
      await screen.findByRole("heading", { name: /Жизненный цикл/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /^Управляющие креды$/ }),
    ).toBeInTheDocument();
    // mgmt-учётка из фикстуры и кнопка ротации.
    expect(await screen.findByText("dbosmgr")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Ротировать управляющие креды/ }),
    ).toBeInTheDocument();
  });

  it("неподготовленная ВМ показывает кнопку «Подготовить»", async () => {
    // vm-103 в фикстуре is_managed=false.
    renderVm("/vm?hub=srv-07&id=vm-103");
    await openVmTab("Управление");
    expect(
      await screen.findByRole("button", { name: /Prepare/ }),
    ).toBeInTheDocument();
  });

  it("открывает раздел IP-пулов и рендерит список из фикстур", async () => {
    renderVm("/vm");
    fireEvent.click(await screen.findByRole("button", { name: /IP-пулы \(IPAM\)/ }));
    expect(
      await screen.findByRole("heading", { name: /IP-пулы \(IPAM\)/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("core-lan")).toBeInTheDocument();
    expect(screen.getByText("dev-lan")).toBeInTheDocument();
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

  it("вкладка «Питание» несёт тумблер автозапуска и переключает его", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    await openVmTab("Питание");
    // vm-101 в фикстуре autostart=true.
    const toggle = await screen.findByRole("switch", { name: /Автозапуск/ });
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);
    // После клика (mock) — выключен.
    expect(await screen.findByRole("switch", { name: /Автозапуск/ })).not.toBeChecked();
  });

  it("вкладка «Консоль» несёт панель консоли с выбором SSH/VNC/serial/SPICE", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    await openVmTab("Консоль");
    expect(await screen.findByRole("button", { name: /^SSH$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^VNC$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Serial$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^SPICE$/ })).toBeInTheDocument();
  });

  it("консоль SSH = выбор учётки + терминал, как у серверной консоли", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    await openVmTab("Консоль");
    // SSH по умолчанию: тот же account-picker + терминал (кнопка «Подключить»),
    // что и у серверной консоли, а не отдельная data-вёрстка.
    await screen.findByText(/Аккаунт для подключения/);
    expect(dropdownTriggerNear(/Аккаунт для подключения/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Подключить/ }),
    ).toBeInTheDocument();
  });

  it("консоль VNC встраивает вьювер и показывает ws-эндпоинт прокси", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    await openVmTab("Консоль");
    fireEvent.click(await screen.findByRole("button", { name: /^VNC$/ }));
    fireEvent.click(
      screen.getByRole("button", { name: /Подключиться \(VNC\)/ }),
    );
    expect(
      (await screen.findAllByText(/wss:\/\/vms-console\.local/)).length,
    ).toBeGreaterThan(0);
  });

  it("пакеты ВМ: pattern-input и секция истории (как у сервера)", async () => {
    renderVm("/vm?hub=srv-07&id=vm-101");
    await openVmTab("Пакеты");
    // Панель пакетов ВМ несёт тот же pattern-input, что и серверная.
    expect(
      await screen.findByPlaceholderText(/linux-image/),
    ).toBeInTheDocument();
    // И секцию «История запросов» из общего под-компонента.
    expect(screen.getByText("История запросов")).toBeInTheDocument();
    // В mock-истории есть прошлый запрос с паттерном linux-image*.
    expect(await screen.findByText("linux-image*")).toBeInTheDocument();
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
