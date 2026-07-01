import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { ConfirmProvider } from "@/components/ui/ConfirmDialog";

// Мокаем только сетевой клиент — wrapper'ы аккаунтов и обработчики вкладки
// идут как есть, чтобы проверить реальную связку handler → accountsApi →
// apiDelete (метод, путь, тело отвязки).
const apiGetMock = vi.fn();
const apiPostMock = vi.fn();
const apiPatchMock = vi.fn();
const apiDeleteMock = vi.fn();
vi.mock("@/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/client")>();
  return {
    ...actual,
    apiGet: (...a: unknown[]) => apiGetMock(...a),
    apiPost: (...a: unknown[]) => apiPostMock(...a),
    apiPatch: (...a: unknown[]) => apiPatchMock(...a),
    apiDelete: (...a: unknown[]) => apiDeleteMock(...a),
  };
});

vi.mock("@/lib/labels", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/labels")>();
  return {
    ...actual,
    useServerMap: () => new Map<string, string>([["srv1", "alpha"]]),
    useUserLabel: () => "—",
  };
});

import { AccountsTab } from "@/pages/server/tabs/accounts";

const ACCOUNT = {
  id: "acc1",
  server_ids: ["srv1", "srv2"],
  department_id: "dep1",
  login: "dbos-svc",
  source: "managed" as const,
  has_sudo: true,
  unix_groups: ["docker"],
  linked_user_id: null,
  shell: "/bin/bash",
  home_dir: "/home/dbos-svc",
  is_active: true,
  password_rotated_at: "2026-01-02T00:00:00Z",
  password_b64: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  created_by: null,
};

function renderTab() {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <ConfirmProvider>
            <MemoryRouter>
              <AccountsTab serverId="srv1" />
            </MemoryRouter>
          </ConfirmProvider>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

describe("AccountsTab — unbind", () => {
  beforeEach(() => {
    window.localStorage.clear();
    apiGetMock.mockReset();
    apiPostMock.mockReset();
    apiPatchMock.mockReset();
    apiDeleteMock.mockReset();
    // Список аккаунтов сервера.
    apiGetMock.mockResolvedValue({
      items: [ACCOUNT],
      total: 1,
      limit: 200,
      offset: 0,
    });
    apiDeleteMock.mockResolvedValue({ ...ACCOUNT, server_ids: ["srv2"] });
  });

  it("Unbind зовёт DELETE /server-accounts/{id}/servers с телом {server_ids}", async () => {
    renderTab();

    // Дождаться строки аккаунта и открыть детали.
    const row = await screen.findByText("dbos-svc");
    fireEvent.click(row);

    const unbindBtn = await screen.findByRole("button", { name: /Unbind/i });
    expect(unbindBtn).not.toBeDisabled();
    fireEvent.click(unbindBtn);

    // Подтвердить в модалке.
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Отвязать" }));

    await waitFor(() => expect(apiDeleteMock).toHaveBeenCalledTimes(1));
    const [path, body] = apiDeleteMock.mock.calls[0];
    expect(path).toBe("/server/v1/server-accounts/acc1/servers");
    expect(body).toEqual({ server_ids: ["srv1"] });
  });

  it("слабый пароль отбивается клиентом с понятным текстом, POST не уходит", async () => {
    renderTab();

    const createBtn = await screen.findByRole("button", {
      name: /Создать аккаунт/i,
    });
    fireEvent.click(createBtn);

    fireEvent.change(screen.getByPlaceholderText("dbos-svc"), {
      target: { value: "svc-weak" },
    });
    // Пароль короче политики (< 8 символов) — клиентская проверка должна
    // остановить submit до сети.
    fireEvent.change(screen.getByPlaceholderText("—"), {
      target: { value: "short" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Создать" }));

    expect(
      await screen.findByText(/Пароль не соответствует политике/),
    ).toBeInTheDocument();
    expect(apiPostMock).not.toHaveBeenCalled();
  });

  it("Unbind доступен и диспатчится на единственном сервере аккаунта", async () => {
    // Backend больше не блокирует отвязку последнего сервера — кнопка активна.
    apiGetMock.mockResolvedValue({
      items: [{ ...ACCOUNT, server_ids: ["srv1"] }],
      total: 1,
      limit: 200,
      offset: 0,
    });
    apiDeleteMock.mockResolvedValue({ ...ACCOUNT, server_ids: [] });
    renderTab();

    const row = await screen.findByText("dbos-svc");
    fireEvent.click(row);

    const unbindBtn = await screen.findByRole("button", { name: /Unbind/i });
    expect(unbindBtn).not.toBeDisabled();
    fireEvent.click(unbindBtn);

    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Отвязать" }));

    await waitFor(() => expect(apiDeleteMock).toHaveBeenCalledTimes(1));
    const [path, body] = apiDeleteMock.mock.calls[0];
    expect(path).toBe("/server/v1/server-accounts/acc1/servers");
    expect(body).toEqual({ server_ids: ["srv1"] });
  });
});
