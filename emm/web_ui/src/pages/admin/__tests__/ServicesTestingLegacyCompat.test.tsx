import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { ConfirmProvider } from "@/components/ui/ConfirmDialog";
import { ApiError } from "@/api/client";

const getSettingsMock = vi.fn();
const updateSettingsMock = vi.fn();
const listNetworksMock = vi.fn();
const createNetworkMock = vi.fn();
const updateNetworkMock = vi.fn();
const deleteNetworkMock = vi.fn();
const resolveMock = vi.fn();
vi.mock("@/api/testing/legacyCompat", () => ({
  get getLegacyCompatSettings() {
    return getSettingsMock;
  },
  get updateLegacyCompatSettings() {
    return updateSettingsMock;
  },
  get listCompatNetworks() {
    return listNetworksMock;
  },
  get createCompatNetwork() {
    return createNetworkMock;
  },
  get updateCompatNetwork() {
    return updateNetworkMock;
  },
  get deleteCompatNetwork() {
    return deleteNetworkMock;
  },
  get resolveCompatIp() {
    return resolveMock;
  },
}));

const listDepartmentsMock = vi.fn();
vi.mock("@/api/auth/departments", () => ({
  get listDepartments() {
    return listDepartmentsMock;
  },
}));

import { ServicesTestingLegacyCompat } from "@/pages/admin/services/ServicesTestingLegacyCompat";

const SEED = {
  id: "cnet_stands_legacy",
  cidr: "10.177.103.0/24",
  description: "Подсеть стендов и ВМ легаси",
  enabled: true,
};

function renderPage() {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <ConfirmProvider>
          <ServicesTestingLegacyCompat />
        </ConfirmProvider>
      </ToastProvider>
    </ThemeProvider>,
  );
}

describe("ServicesTestingLegacyCompat", () => {
  beforeEach(() => {
    for (const m of [
      getSettingsMock, updateSettingsMock, listNetworksMock, createNetworkMock,
      updateNetworkMock, deleteNetworkMock, resolveMock, listDepartmentsMock,
    ]) m.mockReset();
    getSettingsMock.mockResolvedValue({ default_department_id: null });
    listNetworksMock.mockResolvedValue([SEED]);
    listDepartmentsMock.mockResolvedValue([
      { id: "dep_qa", name: "Отдел нагрузочного тестирования", user_count: 0, created_at: "2026-01-01T00:00:00Z" },
    ]);
  });

  it("показывает сидовую подсеть и предупреждает, что отдел по умолчанию не выбран", async () => {
    renderPage();
    expect(await screen.findByTestId("compat-network-10.177.103.0/24")).toHaveTextContent("Подсеть стендов");
    expect(await screen.findByText(/Отдел не выбран/)).toBeInTheDocument();
  });

  it("выбор отдела по умолчанию из списка отделов и сохранение", async () => {
    updateSettingsMock.mockResolvedValue({ default_department_id: "dep_qa" });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /Отдел по умолчанию:/ }));
    fireEvent.click(await screen.findByRole("option", { name: /Отдел нагрузочного тестирования/ }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({ default_department_id: "dep_qa" }));
  });

  it("без доступа к списку отделов — поле для id отдела", async () => {
    listDepartmentsMock.mockRejectedValue(new ApiError(403, { message: "forbidden" }));
    getSettingsMock.mockResolvedValue({ default_department_id: "dep_old" });
    updateSettingsMock.mockResolvedValue({ default_department_id: null });
    renderPage();
    const input = (await screen.findByDisplayValue("dep_old")) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(updateSettingsMock).toHaveBeenCalledWith({ default_department_id: null }));
  });

  it("добавляет подсеть и показывает ошибку backend'а", async () => {
    createNetworkMock.mockResolvedValueOnce({ ...SEED, id: "cnet_x", cidr: "10.10.0.0/16", description: "лаба" });
    renderPage();
    await screen.findByTestId("compat-network-10.177.103.0/24");
    fireEvent.change(screen.getByPlaceholderText("10.177.103.0/24"), { target: { value: " 10.10.0.0/16 " } });
    fireEvent.change(screen.getByPlaceholderText("стенды отдела"), { target: { value: "лаба" } });
    fireEvent.click(screen.getByRole("button", { name: /Добавить подсеть/ }));
    await waitFor(() =>
      expect(createNetworkMock).toHaveBeenCalledWith({ cidr: "10.10.0.0/16", description: "лаба" }),
    );

    createNetworkMock.mockRejectedValueOnce(new ApiError(409, { message: "duplicate" }));
    fireEvent.change(screen.getByPlaceholderText("10.177.103.0/24"), { target: { value: "10.177.103.0/24" } });
    fireEvent.click(screen.getByRole("button", { name: /Добавить подсеть/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Такая подсеть уже есть");
  });

  it("не отправляет явно некорректную подсеть", async () => {
    renderPage();
    await screen.findByTestId("compat-network-10.177.103.0/24");
    fireEvent.change(screen.getByPlaceholderText("10.177.103.0/24"), { target: { value: "стенды" } });
    fireEvent.click(screen.getByRole("button", { name: /Добавить подсеть/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("например 10.177.103.0/24");
    expect(createNetworkMock).not.toHaveBeenCalled();
  });

  it("выключает подсеть переключателем", async () => {
    updateNetworkMock.mockResolvedValue({ ...SEED, enabled: false });
    renderPage();
    fireEvent.click(await screen.findByLabelText("Подсеть 10.177.103.0/24 включена"));
    await waitFor(() => expect(updateNetworkMock).toHaveBeenCalledWith("cnet_stands_legacy", { enabled: false }));
  });

  it("проверка адреса показывает отдел и причину выбора", async () => {
    resolveMock.mockResolvedValue({
      ip: "10.177.103.204", allowed: true, network_id: "cnet_stands_legacy", cidr: "10.177.103.0/24",
      department_id: "dep_qa", reason: "stand", stand_ids: ["stand_3"],
    });
    renderPage();
    fireEvent.change(await screen.findByPlaceholderText("10.177.103.201"), { target: { value: "10.177.103.204" } });
    fireEvent.click(screen.getByRole("button", { name: /Проверить/ }));
    const result = await screen.findByTestId("compat-resolve-result");
    expect(resolveMock).toHaveBeenCalledWith("10.177.103.204");
    expect(result).toHaveTextContent("доступ есть");
    expect(result).toHaveTextContent("Отдел нагрузочного тестирования");
    expect(result).toHaveTextContent("найден стенд с этим IP");
    expect(result).toHaveTextContent("stand_3");
  });
});
