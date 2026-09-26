import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";

const getMock = vi.fn();
const putMock = vi.fn();
vi.mock("@/api/server/vmTestSettings", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/server/vmTestSettings")>();
  return {
    ...actual,
    get getVmTestSettings() {
      return getMock;
    },
    get putVmTestSettings() {
      return putMock;
    },
  };
});

import { ServicesVmTestSnapshots } from "@/pages/admin/services/ServicesVmTestSnapshots";
import { validateVmSnapshotTemplates } from "@/api/server/vmTestSettings";
import { visibleItems, buildAdminItems } from "@/pages/admin/adminCatalog";
import type { Persona } from "@/types/persona";

function renderPage() {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <ServicesVmTestSnapshots />
      </ToastProvider>
    </ThemeProvider>,
  );
}

function persona(role: string | null): Persona {
  return { platform_role: role, service_roles: {}, has_admin: false } as unknown as Persona;
}

describe("ServicesVmTestSnapshots — шаблоны имени снимка ВМ", () => {
  beforeEach(() => {
    getMock.mockReset();
    putMock.mockReset();
    getMock.mockResolvedValue({ snapshot_name_templates: ["{version}", "{version}_{mode}"] });
    putMock.mockImplementation(async (body) => body);
  });

  it("показывает шаблоны по строке и сохраняет изменённый список", async () => {
    renderPage();
    const area = (await screen.findByLabelText("Шаблоны имени снимка")) as HTMLTextAreaElement;
    expect(area.value).toBe("{version}\n{version}_{mode}");
    const save = screen.getByRole("button", { name: "Сохранить" });
    expect(save).toBeDisabled();

    fireEvent.change(area, { target: { value: "{hostname}-{version}\n{version}\n" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() =>
      expect(putMock).toHaveBeenCalledWith({ snapshot_name_templates: ["{hostname}-{version}", "{version}"] }),
    );
  });

  it("шаблон без {version} не даёт сохранить", async () => {
    renderPage();
    const area = await screen.findByLabelText("Шаблоны имени снимка");
    fireEvent.change(area, { target: { value: "{hostname}" } });
    expect(await screen.findByText(/«\{hostname\}»: \{version\} должен встречаться ровно один раз/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  });

  it("клиентская проверка совпадает с правилами backend'а", () => {
    expect(validateVmSnapshotTemplates(["{version}", "{version}_{mode}"])).toBeNull();
    expect(validateVmSnapshotTemplates([])).not.toBeNull();
    expect(validateVmSnapshotTemplates(["{version}-{version}"])).not.toBeNull();
    expect(validateVmSnapshotTemplates(["{release}"])).toMatch(/неизвестные/);
    expect(validateVmSnapshotTemplates(["{version}", "{version}"])).toMatch(/повторяются/);
  });

  it("пункт каталога виден только account_admin", () => {
    const items = buildAdminItems([]);
    const id = "services.server.vm_test_snapshots";
    expect(visibleItems(items, persona("account_admin")).some((item) => item.id === id)).toBe(true);
    expect(visibleItems(items, persona("dep_admin")).some((item) => item.id === id)).toBe(false);
  });
});
