import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";

const getMock = vi.fn();
const putMock = vi.fn();
vi.mock("@/api/server/probeSettings", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("@/api/server/probeSettings")
  >();
  return {
    ...actual,
    get getProbeSettings() {
      return getMock;
    },
    get putProbeSettings() {
      return putMock;
    },
  };
});

import { ServicesProbeSettings } from "@/pages/admin/services/ServicesProbeSettings";
import { visibleItems, buildAdminItems } from "@/pages/admin/adminCatalog";
import type { Persona } from "@/types/persona";

function makeConfig() {
  return {
    reachability_probe_interval_seconds: 60,
    power_probe_interval_seconds: 300,
    reachability_probe_enabled: true,
    power_probe_enabled: true,
  };
}

function renderPage() {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <ServicesProbeSettings />
      </ToastProvider>
    </ThemeProvider>,
  );
}

function persona(role: string | null): Persona {
  return {
    platform_role: role,
    service_roles: {},
    has_admin: false,
  } as unknown as Persona;
}

describe("ServicesProbeSettings", () => {
  beforeEach(() => {
    getMock.mockReset();
    putMock.mockReset();
    getMock.mockResolvedValue(makeConfig());
    putMock.mockResolvedValue(makeConfig());
  });

  it("грузит и показывает поля настроек", async () => {
    renderPage();
    expect(await screen.findByDisplayValue("60")).toBeInTheDocument();
    expect(screen.getByDisplayValue("300")).toBeInTheDocument();
    expect(screen.getByText(/Доступность/)).toBeInTheDocument();
    expect(screen.getByText(/Питание/)).toBeInTheDocument();
  });

  it("правка интервала и сохранение зовёт PUT с верным payload", async () => {
    renderPage();
    const reach = await screen.findByDisplayValue("60");
    fireEvent.change(reach, { target: { value: "90" } });

    fireEvent.click(screen.getByRole("button", { name: /Сохранить/ }));

    await waitFor(() => expect(putMock).toHaveBeenCalledTimes(1));
    const payload = putMock.mock.calls[0][0];
    expect(payload.reachability_probe_interval_seconds).toBe(90);
    expect(payload.power_probe_interval_seconds).toBe(300);
  });

  it("блокирует сохранение, когда power < reachability", async () => {
    renderPage();
    const reach = await screen.findByDisplayValue("60");
    // reachability выше power (300) — ошибка порядка.
    fireEvent.change(reach, { target: { value: "600" } });

    expect(
      await screen.findByText(/питания не может быть короче/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Сохранить/ })).toBeDisabled();
    expect(putMock).not.toHaveBeenCalled();
  });

  it("пункт каталога виден account_admin и скрыт для остальных", () => {
    const items = buildAdminItems([]);
    const adminVisible = visibleItems(items, persona("account_admin"));
    const depVisible = visibleItems(items, persona("dep_admin"));
    expect(
      adminVisible.some((i) => i.id === "services.server.probe_settings"),
    ).toBe(true);
    expect(
      depVisible.some((i) => i.id === "services.server.probe_settings"),
    ).toBe(false);
  });
});
