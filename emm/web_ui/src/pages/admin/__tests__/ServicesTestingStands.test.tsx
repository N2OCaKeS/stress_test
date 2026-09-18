import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { ConfirmProvider } from "@/components/ui/ConfirmDialog";
import { PersonaProvider } from "@/contexts/PersonaContext";

const listTestStandsMock = vi.fn();
const getTestStandMock = vi.fn();
const createTestStandMock = vi.fn();
const updateTestStandMock = vi.fn();
const deleteTestStandMock = vi.fn();
const getTestStandCredentialsMock = vi.fn();
vi.mock("@/api/testing/testStands", () => ({
  listTestStands: (...a: unknown[]) => listTestStandsMock(...a),
  getTestStand: (...a: unknown[]) => getTestStandMock(...a),
  createTestStand: (...a: unknown[]) => createTestStandMock(...a),
  updateTestStand: (...a: unknown[]) => updateTestStandMock(...a),
  deleteTestStand: (...a: unknown[]) => deleteTestStandMock(...a),
  getTestStandCredentials: (...a: unknown[]) => getTestStandCredentialsMock(...a),
}));

const listServersMock = vi.fn();
vi.mock("@/api/server/servers", () => ({
  listServers: (...a: unknown[]) => listServersMock(...a),
}));

const listOsVersionsMock = vi.fn();
const resolveOsKernelsMock = vi.fn();
vi.mock("@/api/server/osVersions", () => ({
  listOsVersions: (...a: unknown[]) => listOsVersionsMock(...a),
  resolveOsKernels: (...a: unknown[]) => resolveOsKernelsMock(...a),
}));

const previewTestRunMock = vi.fn();
const createTestRunMock = vi.fn();
vi.mock("@/api/testing/testRuns", () => ({
  previewTestRun: (...a: unknown[]) => previewTestRunMock(...a),
  createTestRun: (...a: unknown[]) => createTestRunMock(...a),
}));

import { ServicesTestingStands } from "@/pages/admin/services/ServicesTestingStands";

function renderAdminStands() {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <ToastProvider>
          <ConfirmProvider>
            <PersonaProvider>
              <ServicesTestingStands />
            </PersonaProvider>
          </ConfirmProvider>
        </ToastProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe("ServicesTestingStands — управление стендами пула", () => {
  beforeEach(() => {
    import.meta.env.VITE_USE_MOCK_AUTH = "false";
    listTestStandsMock.mockReset();
    getTestStandMock.mockReset();
    createTestStandMock.mockReset();
    updateTestStandMock.mockReset();
    deleteTestStandMock.mockReset();
    getTestStandCredentialsMock.mockReset();
    listServersMock.mockReset();
    listOsVersionsMock.mockReset();
    resolveOsKernelsMock.mockReset();
    previewTestRunMock.mockReset();
    createTestRunMock.mockReset();

    listOsVersionsMock.mockResolvedValue({
      items: [{ id: "osv_1", name: "1.8.5", kernels: ["6.1"] }],
      total: 1,
      limit: 500,
      offset: 0,
    });
    previewTestRunMock.mockResolvedValue({ stands_without_tests: [], entries: [] });

    listServersMock.mockResolvedValue({
      items: [{ id: "srv_2", hostname: "stand-02", display_name: "stand-02", ip_address: "10.177.103.202" }],
      total: 1,
      limit: 500,
      offset: 0,
    });
    listTestStandsMock.mockResolvedValue({
      items: [{ id: "ts_1", server_id: "srv_1", department_id: "dep_1", queue_enabled: true, is_active: true }],
      total: 1,
      limit: 500,
      offset: 0,
    });
    getTestStandMock.mockResolvedValue({
      id: "ts_1",
      server_id: "srv_1",
      department_id: "dep_1",
      queue_enabled: true,
      is_active: true,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
      created_by: null,
      server: { hostname: "stand-live-01", display_name: "stand-live-01", ip_address: "10.177.103.201", busy_state: "testing", os_version_id: "osv_1" },
      server_unavailable: false,
    });
  });

  afterEach(() => {
    import.meta.env.VITE_USE_MOCK_AUTH = "true";
  });

  it("«Добавить стенд» выбирает сервер из инвентаря и вызывает createTestStand", async () => {
    createTestStandMock.mockResolvedValue({
      id: "ts_2",
      server_id: "srv_2",
      department_id: "dep_1",
      queue_enabled: true,
      is_active: true,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
      created_by: null,
      server: null,
      server_unavailable: false,
    });
    renderAdminStands();
    await screen.findAllByText("stand-live-01");

    fireEvent.click(screen.getByRole("button", { name: /Добавить стенд/ }));
    await screen.findByRole("heading", { name: "Добавить стенд" });

    fireEvent.click(await screen.findByRole("button", { name: "Выберите сервер" }));
    fireEvent.click(await screen.findByRole("option", { name: /stand-02/ }));
    fireEvent.click(screen.getByRole("button", { name: "Добавить" }));

    await waitFor(() =>
      expect(createTestStandMock).toHaveBeenCalledWith({
        server_id: "srv_2",
        queue_enabled: true,
        is_active: true,
      }),
    );
    // модалка закрылась, список стендов перезагружен
    await waitFor(() => expect(listTestStandsMock).toHaveBeenCalledTimes(2));
  });

  it("toggle «Очередь» вызывает updateTestStand с инвертированным queue_enabled", async () => {
    updateTestStandMock.mockResolvedValue({});
    renderAdminStands();
    await screen.findAllByText("stand-live-01");

    fireEvent.click(screen.getByRole("button", { name: "включена" }));

    await waitFor(() =>
      expect(updateTestStandMock).toHaveBeenCalledWith("ts_1", { queue_enabled: false }),
    );
  });

  it("удаление стенда запрашивает подтверждение и вызывает deleteTestStand", async () => {
    deleteTestStandMock.mockResolvedValue({ ok: true });
    renderAdminStands();
    await screen.findAllByText("stand-live-01");

    fireEvent.click(screen.getByRole("button", { name: "Удалить стенд" }));
    fireEvent.click(await screen.findByRole("button", { name: "Удалить" }));

    await waitFor(() => expect(deleteTestStandMock).toHaveBeenCalledWith("ts_1"));
  });

  it("«Учётные данные теста» — сначала метаданные без reveal, «Показать» раскрывает пароль", async () => {
    getTestStandCredentialsMock.mockImplementation((_id: string, reveal?: boolean) =>
      Promise.resolve(
        reveal
          ? {
              exists: true,
              username: "test_runner",
              ssh_public_key: "ssh-ed25519 AAAA...",
              rotated_at: "2026-09-01T00:00:00Z",
              password_b64: btoa("s3cr3t"),
              ssh_private_key_b64: null,
            }
          : {
              exists: true,
              username: "test_runner",
              ssh_public_key: "ssh-ed25519 AAAA...",
              rotated_at: "2026-09-01T00:00:00Z",
              password_b64: null,
              ssh_private_key_b64: null,
            },
      ),
    );
    renderAdminStands();
    await screen.findAllByText("stand-live-01");

    fireEvent.click(screen.getByRole("button", { name: /Учётные данные теста/ }));
    await waitFor(() => expect(getTestStandCredentialsMock).toHaveBeenCalledWith("ts_1"));
    expect(await screen.findByText("test_runner")).toBeInTheDocument();
    expect(screen.queryByText("s3cr3t")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Показать" }));
    await waitFor(() => expect(getTestStandCredentialsMock).toHaveBeenCalledWith("ts_1", true));
    expect(await screen.findByText("s3cr3t")).toBeInTheDocument();
  });

  it("«Запустить все тесты стенда» открывает модалку и запрашивает предпросмотр по выбранному стенду", async () => {
    previewTestRunMock.mockResolvedValue({
      stands_without_tests: [],
      entries: [
        { stand_id: "ts_1", test_id: "t_1", test_code: "T1", test_name: "Тест один", kernel: "6.1", action: "launch", reason: null },
        { stand_id: "ts_1", test_id: "t_2", test_code: "T2", test_name: "Тест два", kernel: "6.1", action: "skip_stand_inactive", reason: "Стенд неактивен" },
      ],
    });
    renderAdminStands();
    await screen.findAllByText("stand-live-01");

    fireEvent.click(screen.getByRole("button", { name: /Запустить все тесты стенда/ }));
    await screen.findByRole("heading", { name: "Запустить все тесты стенда" });

    fireEvent.click(await screen.findByRole("button", { name: "Выберите РЦ" }));
    fireEvent.click(await screen.findByRole("option", { name: "1.8.5" }));

    await waitFor(() =>
      expect(previewTestRunMock).toHaveBeenCalledWith({
        os_version_id: "osv_1",
        kernel: "6.1",
        test_run_stands: ["ts_1"],
        final: false,
        debug: false,
      }),
    );
    expect(await screen.findByText("будет запущен")).toBeInTheDocument();
    expect(await screen.findByText("Стенд неактивен")).toBeInTheDocument();
  });

  it("«Запустить группу» вызывает createTestRun с test_run_stands=[standId] и request_id, «Готово» закрывает модалку", async () => {
    previewTestRunMock.mockResolvedValue({
      stands_without_tests: [],
      entries: [
        { stand_id: "ts_1", test_id: "t_1", test_code: "T1", test_name: "Тест один", kernel: "6.1", action: "launch", reason: null },
      ],
    });
    createTestRunMock.mockResolvedValue({
      id: "run_1",
      os_version_id: "osv_1",
      mode: "orel",
      kernel: "6.1",
      department_id: "dep_1",
      test_run_stands: ["ts_1"],
      status: "queued",
      final: false,
      created_at: "2026-09-15T00:00:00Z",
      updated_at: "2026-09-15T00:00:00Z",
      created_by: null,
      stands_without_tests: [],
      enqueue_errors: [],
    });
    renderAdminStands();
    await screen.findAllByText("stand-live-01");

    fireEvent.click(screen.getByRole("button", { name: /Запустить все тесты стенда/ }));
    await screen.findByRole("heading", { name: "Запустить все тесты стенда" });

    fireEvent.click(await screen.findByRole("button", { name: "Выберите РЦ" }));
    fireEvent.click(await screen.findByRole("option", { name: "1.8.5" }));
    await screen.findByText("будет запущен");

    fireEvent.click(screen.getByRole("button", { name: "Запустить группу" }));

    await waitFor(() => expect(createTestRunMock).toHaveBeenCalledTimes(1));
    const call = createTestRunMock.mock.calls[0][0];
    expect(call).toMatchObject({
      os_version_id: "osv_1",
      kernel: "6.1",
      test_run_stands: ["ts_1"],
      final: false,
    });
    expect(call).not.toHaveProperty("mode");
    expect(typeof call.request_id).toBe("string");
    expect(call.request_id.length).toBeGreaterThanOrEqual(8);

    fireEvent.click(await screen.findByRole("button", { name: "Готово" }));
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Группа запущена" })).not.toBeInTheDocument());
  });
});
