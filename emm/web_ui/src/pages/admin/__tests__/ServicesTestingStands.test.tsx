import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { ConfirmProvider } from "@/components/ui/ConfirmDialog";

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

import { ServicesTestingStands } from "@/pages/admin/services/ServicesTestingStands";

function renderAdminStands() {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <ToastProvider>
          <ConfirmProvider>
            <ServicesTestingStands />
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
});
