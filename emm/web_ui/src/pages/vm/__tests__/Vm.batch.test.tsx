import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { CreateVmPane } from "@/pages/vm/Vm";
import type {
  VmBulkCreateResponse,
  VmCreateRequest,
  VmHub,
  VmImage,
} from "@/api/server/vms";

const HUB: VmHub = {
  id: "srv-07",
  hostname: "srv-node-07",
  display_name: "kvm-hub-core-1",
  ip_address: "10.177.103.207",
  department_id: "core",
  vm_count: 0,
};

const IMAGES: VmImage[] = [
  {
    name: "vm_station",
    kind: "universal",
    description: "Universal-станция",
    os_versions: ["1.7.5.9", "1.8.1.6"],
    min_disk_gb: 30,
  },
];

function renderPane(onSubmit: (items: VmCreateRequest[]) => Promise<VmBulkCreateResponse>) {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <MemoryRouter>
            <CreateVmPane
              hub={HUB}
              images={IMAGES}
              mock
              onRefreshImages={() => {}}
              onCancel={() => {}}
              onSubmit={onSubmit}
              onChanged={() => {}}
            />
          </MemoryRouter>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

function makeOnSubmit() {
  return vi.fn(
    async (items: VmCreateRequest[]): Promise<VmBulkCreateResponse> => ({
      results: items.map((it) => ({
        name: it.name,
        status: "queued" as const,
        task_id: `t-${it.name}`,
      })),
    }),
  );
}

describe("CreateVmPane (батч-форма)", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("добавляет и сворачивает блоки, шлёт /vms/bulk со всеми items и рендерит результат", async () => {
    const onSubmit = makeOnSubmit();
    renderPane(onSubmit);

    // Блок #1.
    fireEvent.change(screen.getByPlaceholderText("alse-1.8-rc"), {
      target: { value: "vm-a" },
    });
    // «Добавить ВМ» сворачивает #1 и открывает #2.
    fireEvent.click(screen.getByRole("button", { name: /Добавить ВМ/ }));
    // Свёрнутый #1 показывает своё имя как заголовок; открытый один блок с полем.
    expect(screen.getByText("vm-a")).toBeInTheDocument();
    expect(screen.getAllByPlaceholderText("alse-1.8-rc")).toHaveLength(1);

    fireEvent.change(screen.getByPlaceholderText("alse-1.8-rc"), {
      target: { value: "vm-b" },
    });

    fireEvent.click(screen.getByRole("button", { name: /Создать все/ }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const items = onSubmit.mock.calls[0][0];
    expect(items).toHaveLength(2);
    expect(items.map((i) => i.name)).toEqual(["vm-a", "vm-b"]);

    // Per-VM результат.
    expect(
      await screen.findByText("Результат создания 2 ВМ"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("создана")).toHaveLength(2);
  });

  it("диск ниже минимума бокса даёт предупреждение и блокирует сабмит", async () => {
    const onSubmit = makeOnSubmit();
    renderPane(onSubmit);

    fireEvent.change(screen.getByPlaceholderText("alse-1.8-rc"), {
      target: { value: "vm-small" },
    });
    // vm_station.min_disk_gb = 30 → 5 ГБ ниже минимума.
    fireEvent.change(screen.getByLabelText(/Диск/), { target: { value: "5" } });

    expect(screen.getByText(/Меньше минимума бокса/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Создать все/ })).toBeDisabled();
  });

  it("привязка учёток уходит в accounts[] элемента", async () => {
    const onSubmit = makeOnSubmit();
    renderPane(onSubmit);

    fireEvent.change(screen.getByPlaceholderText("alse-1.8-rc"), {
      target: { value: "vm-acc" },
    });
    // Учётки отдела core грузятся из моков.
    const label = (await screen.findByText("tester")).closest("label");
    const checkbox = label?.querySelector(
      'input[type="checkbox"]',
    ) as HTMLInputElement;
    fireEvent.click(checkbox);

    fireEvent.click(screen.getByRole("button", { name: /Создать все/ }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
    const items = onSubmit.mock.calls[0][0];
    expect(items[0].accounts).toContain("acc-core-tester");
  });
});
