import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { ConfirmProvider } from "@/components/ui/ConfirmDialog";

const getHostServicesSettingsMock = vi.fn();
const updateHostServicesSettingsMock = vi.fn();
const listHostSshCredentialsMock = vi.fn();
vi.mock("@/api/server/hostServicesSettings", () => ({
  get getHostServicesSettings() {
    return getHostServicesSettingsMock;
  },
  get updateHostServicesSettings() {
    return updateHostServicesSettingsMock;
  },
  get listHostSshCredentials() {
    return listHostSshCredentialsMock;
  },
  listHostServiceUnits: async () => ({ items: [] }),
  createHostServiceUnit: vi.fn(),
  renameHostServiceUnit: vi.fn(),
  deleteHostServiceUnit: vi.fn(),
}));

vi.mock("@/contexts/PersonaContext", () => ({
  usePersona: () => ({
    persona: { id: "u1", dept_id: "dep_a", platform_role: "dep_admin", service_roles: {} },
  }),
}));

import { ServicesHostControl } from "@/pages/admin/services/ServicesHostControl";

function makeSettings(over: Record<string, unknown> = {}) {
  return {
    configured: false,
    ssh_host: null,
    ssh_port: 22,
    ssh_user: null,
    private_key_is_set: false,
    credential_id: null,
    legacy_private_key_is_set: false,
    ...over,
  };
}

function renderPage() {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <ConfirmProvider>
          <ServicesHostControl />
        </ConfirmProvider>
      </ToastProvider>
    </ThemeProvider>,
  );
}

describe("ServicesHostControl", () => {
  beforeEach(() => {
    getHostServicesSettingsMock.mockReset();
    updateHostServicesSettingsMock.mockReset();
    listHostSshCredentialsMock.mockReset();
    listHostSshCredentialsMock.mockResolvedValue([
      { id: "cred_host", name: "Ключ ALLTA-хоста", owner_dept_id: "dep_a", valid_from: null, valid_to: null },
    ]);
  });

  it("показывает баннер старого хранения, пока ключ не привязан к сервисной записи", async () => {
    getHostServicesSettingsMock.mockResolvedValue(
      makeSettings({ ssh_host: "10.0.0.1", ssh_user: "u", private_key_is_set: true, legacy_private_key_is_set: true }),
    );
    renderPage();
    expect(await screen.findByText(/старое хранение ключа/)).toBeInTheDocument();
    expect(document.querySelector("textarea")).toBeNull();
  });

  it("привязка сервисной записи зовёт updateHostServicesSettings с credential_id", async () => {
    getHostServicesSettingsMock.mockResolvedValue(makeSettings({ ssh_host: "10.0.0.1", ssh_user: "u" }));
    updateHostServicesSettingsMock.mockResolvedValue(makeSettings());
    renderPage();

    await screen.findByDisplayValue("10.0.0.1");
    fireEvent.click(screen.getByRole("button", { name: /^Приватный SSH-ключ:/ }));
    fireEvent.click(await screen.findByRole("option", { name: /Ключ ALLTA-хоста/ }));

    const saveButton = screen.getByRole("button", { name: "Сохранить" });
    fireEvent.click(saveButton);

    await waitFor(() => expect(updateHostServicesSettingsMock).toHaveBeenCalledTimes(1));
    const payload = updateHostServicesSettingsMock.mock.calls[0][0];
    expect(payload.credential_id).toBe("cred_host");
    expect(payload.ssh_private_key).toBeUndefined();
  });
});
