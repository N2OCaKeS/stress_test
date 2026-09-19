import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { ConfirmProvider } from "@/components/ui/ConfirmDialog";
import type { Vm as VmType } from "@/api/server/vms";
// Консоль ВМ рендерится тем же файлом-вкладкой, что и у сервера: общий
// `ConsoleTab` при `entity.kind === "vm"` уходит в VM-ветку с выбором вида.
import { ConsoleTab } from "@/pages/server/tabs/console";

// xterm тащит canvas/matchMedia — в jsdom подменяем терминал заглушкой, как в
// серверном ConsoleTab.test. Нам важен account-picker и переключение видов.
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

const MOCK_VM: VmType = {
  id: "vm-console",
  name: "console-vm",
  number: 7,
  hub_server_id: "srv-07",
  department_id: "core",
  os_version: "1.8.1.6",
  box: "vm_station",
  network_mode: "bridge",
  ip_address: "10.10.0.9",
  status: "free",
  power_state: "on",
  cpu: 2,
  ram_mb: 4096,
  disk_gb: 40,
  autostart: false,
  cred_strategy: "per_snapshot",
  busy_state: "free",
  busy_note: null,
  is_managed: true,
  mgmt_user: "dbosmgr",
  mgmt_creds_rotated_at: null,
  mgmt_creds_pending_apply: false,
  created_at: "2026-06-01T00:00:00Z",
  updated_at: "2026-06-01T00:00:00Z",
  created_by: null,
};

function renderConsole() {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <ConfirmProvider>
          <ConsoleTab
            entity={{
              kind: "vm",
              vm: MOCK_VM,
              mock: true,
              canManage: true,
              onChanged: () => {},
            }}
          />
        </ConfirmProvider>
      </ToastProvider>
    </ThemeProvider>,
  );
}

/** Dropdown-триггер, живущий в той же метке `<label>`, что и её подпись-текст. */
function dropdownTriggerNear(text: string | RegExp) {
  const label = screen.getByText(text).closest("label")!;
  return within(label).getByRole("button");
}

describe("Консоль ВМ — селектор вида + переиспользуемая серверная консоль", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("предлагает 4 вида консоли, SSH выбран по умолчанию", () => {
    renderConsole();
    for (const label of ["SSH", "VNC", "Serial", "SPICE"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    // Активный (дефолтный) вид SSH помечен btn-primary.
    expect(screen.getByRole("button", { name: "SSH" })).toHaveClass("btn-primary");
    expect(screen.getByRole("button", { name: "VNC" })).not.toHaveClass(
      "btn-primary",
    );
  });

  it("SSH-вид рендерит тот же account-picker + терминал, что у сервера", async () => {
    renderConsole();
    // Выбор учётки (общий с серверной консолью) и кнопка «Подключить» терминала.
    await screen.findByText(/Аккаунт для подключения/);
    expect(dropdownTriggerNear(/Аккаунт для подключения/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Подключить/ }),
    ).toBeInTheDocument();
  });

  it("Serial-вид — тоже account-picker + терминал (как ssh)", async () => {
    renderConsole();
    fireEvent.click(screen.getByRole("button", { name: "Serial" }));
    await screen.findByText(/Аккаунт для подключения/);
    expect(dropdownTriggerNear(/Аккаунт для подключения/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Подключить/ }),
    ).toBeInTheDocument();
  });

  it("VNC встраивает вьювер iframe'ом с URL текущего origin, без window.open", async () => {
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
    const { container } = renderConsole();
    fireEvent.click(screen.getByRole("button", { name: "VNC" }));
    fireEvent.click(
      await screen.findByRole("button", { name: /Подключиться \(VNC\)/ }),
    );
    expect(
      await screen.findByText(/Графическая консоль \(VNC\)/),
    ).toBeInTheDocument();

    // Вьювер встроен в рабочую область iframe'ом, а не открыт новым окном.
    const iframe = container.querySelector("iframe");
    expect(iframe).not.toBeNull();
    const src = iframe!.getAttribute("src") ?? "";
    // Origin — текущий (не зашитый emm.devos из ws_url), путь и токен на месте.
    expect(src.startsWith(window.location.origin)).toBe(true);
    expect(src).not.toContain("vms-console.local");
    expect(src).toContain("/vm-console/vnc/vm-console");
    expect(src).toContain("token=mock-vnc-token-9f3a");
    expect(openSpy).not.toHaveBeenCalled();
    openSpy.mockRestore();
  });

  it("токен VNC-сессии скрыт по умолчанию и раскрывается по клику", async () => {
    renderConsole();
    fireEvent.click(screen.getByRole("button", { name: "VNC" }));
    fireEvent.click(
      await screen.findByRole("button", { name: /Подключиться \(VNC\)/ }),
    );
    await screen.findByText(/Графическая консоль \(VNC\)/);

    expect(screen.queryByText("mock-vnc-token-9f3a")).not.toBeInTheDocument();
    expect(screen.getByText("••••••••••••")).toBeInTheDocument();

    fireEvent.click(screen.getByTitle("Показать"));
    expect(screen.getByText("mock-vnc-token-9f3a")).toBeInTheDocument();
    expect(screen.queryByText("••••••••••••")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTitle("Скрыть"));
    expect(screen.queryByText("mock-vnc-token-9f3a")).not.toBeInTheDocument();
    expect(screen.getByText("••••••••••••")).toBeInTheDocument();
  });

  it("SPICE встраивает вьювер iframe'ом в рабочую область", async () => {
    const { container } = renderConsole();
    fireEvent.click(screen.getByRole("button", { name: "SPICE" }));
    fireEvent.click(
      await screen.findByRole("button", { name: /Подключиться \(SPICE\)/ }),
    );
    expect(
      await screen.findByText(/Графическая консоль \(SPICE\)/),
    ).toBeInTheDocument();
    const iframe = container.querySelector("iframe");
    expect(iframe).not.toBeNull();
    expect(iframe!.getAttribute("src")).toContain("/vm-console/spice/vm-console");
  });
});
