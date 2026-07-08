import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";
import type { Vm as VmType } from "@/api/server/vms";
import { ConsoleCard } from "@/pages/vm/Vm";

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
        <ConsoleCard vm={MOCK_VM} mock />
      </ToastProvider>
    </ThemeProvider>,
  );
}

describe("ConsoleCard (VM console selector)", () => {
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

  it("по умолчанию открывает SSH-данные подключения (команда + токен)", () => {
    renderConsole();
    fireEvent.click(screen.getByRole("button", { name: /Открыть консоль/ }));
    // SSH-команда с mgmt-логином и токен доступа.
    expect(screen.getByText(/ssh dbosmgr@10\.10\.0\.9/)).toBeInTheDocument();
    expect(screen.getByText("Токен")).toBeInTheDocument();
  });

  it("VNC даёт кнопку открытия прокси-URL с token в query", () => {
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
    renderConsole();
    fireEvent.click(screen.getByRole("button", { name: /Открыть консоль/ }));
    expect(screen.getByText(/ssh dbosmgr@/)).toBeInTheDocument();

    // Смена вида сбрасывает сессию — открываем заново.
    fireEvent.click(screen.getByRole("button", { name: "VNC" }));
    fireEvent.click(screen.getByRole("button", { name: /Открыть консоль/ }));
    expect(screen.getByText(/Графическая консоль \(VNC\)/)).toBeInTheDocument();
    // SSH-команды на экране больше нет.
    expect(screen.queryByText(/ssh dbosmgr@/)).not.toBeInTheDocument();

    // Кнопка «Открыть консоль VNC» ведёт на http(s)-форму прокси с token.
    fireEvent.click(screen.getByRole("button", { name: /Открыть консоль VNC/ }));
    expect(openSpy).toHaveBeenCalledTimes(1);
    const url = openSpy.mock.calls[0][0] as string;
    expect(url).toContain("/vm-console/vnc/vm-console");
    expect(url).toMatch(/^https:\/\//);
    expect(url).toContain("token=mock-vnc-token-9f3a");
    openSpy.mockRestore();
  });

  it("SPICE показывает графический прокси и кнопку открытия", () => {
    renderConsole();
    fireEvent.click(screen.getByRole("button", { name: "SPICE" }));
    fireEvent.click(screen.getByRole("button", { name: /Открыть консоль/ }));
    expect(screen.getByText(/Графическая консоль \(SPICE\)/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Открыть консоль SPICE/ }),
    ).toBeInTheDocument();
  });

  it("Serial показывает данные serial-консоли (host hub + токен)", () => {
    renderConsole();
    fireEvent.click(screen.getByRole("button", { name: "Serial" }));
    fireEvent.click(screen.getByRole("button", { name: /Открыть консоль/ }));
    expect(
      screen.getByText(/Последовательная консоль \(serial\)/),
    ).toBeInTheDocument();
    expect(screen.getByText("host (hub)")).toBeInTheDocument();
  });
});
