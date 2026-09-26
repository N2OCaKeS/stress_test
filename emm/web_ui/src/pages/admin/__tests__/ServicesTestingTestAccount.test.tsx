import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";

vi.mock("@/contexts/PersonaContext", () => ({
  usePersona: () => ({
    persona: { id: "u1", dept_id: "dep_1", platform_role: "dep_admin", service_roles: {} },
  }),
}));

const getMock = vi.fn();
const putMock = vi.fn();
vi.mock("@/api/testing/departmentTestAccount", () => ({
  getDepartmentTestAccount: (...a: unknown[]) => getMock(...a),
  upsertDepartmentTestAccount: (...a: unknown[]) => putMock(...a),
}));

import { ServicesTestingTestAccount } from "@/pages/admin/services/ServicesTestingTestAccount";
import { buildAdminItems, visibleItems } from "@/pages/admin/adminCatalog";
import type { Persona } from "@/types/persona";

function renderPage() {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <ServicesTestingTestAccount />
      </ToastProvider>
    </ThemeProvider>,
  );
}

const PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIpub test-account";

function account(over: Record<string, unknown> = {}) {
  return {
    department_id: "dep_1",
    configured: false,
    credential_id: null,
    credential_missing: false,
    login: null,
    login_hint: "u",
    has_password: false,
    ssh_public_key: null,
    home_template: "/home/{TEST_USER}",
    home: null,
    updated_at: null,
    ...over,
  };
}

const configured = () =>
  account({
    configured: true,
    credential_id: "cred_1",
    login: "u",
    has_password: true,
    ssh_public_key: PUBLIC_KEY,
    home: "/home/u",
  });

function persona(role: string | null, testing?: string): Persona {
  return {
    platform_role: role,
    service_roles: testing ? { testing } : {},
    has_admin: false,
  } as unknown as Persona;
}

describe("ServicesTestingTestAccount — тестовая учётка отдела", () => {
  beforeEach(() => {
    getMock.mockReset();
    putMock.mockReset();
  });

  it("не настроена: предупреждение, логин из подсказки, пароль обязателен", async () => {
    getMock.mockResolvedValue(account());
    renderPage();
    expect(await screen.findByText(/TEST_ACCOUNT_NOT_CONFIGURED/)).toBeInTheDocument();
    const loginInput = screen.getByPlaceholderText("u") as HTMLInputElement;
    await waitFor(() => expect(loginInput.value).toBe("u"));
    expect(screen.getByText(/будет сгенерирован при первом сохранении/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByText("Задайте пароль тестовой учётки.")).toBeInTheDocument();
    expect(putMock).not.toHaveBeenCalled();
  });

  it("первое сохранение шлёт логин и пароль, показывает публичный ключ", async () => {
    getMock.mockResolvedValue(account());
    putMock.mockResolvedValue(configured());
    renderPage();
    await screen.findByText(/TEST_ACCOUNT_NOT_CONFIGURED/);

    fireEvent.change(screen.getByPlaceholderText("обязателен"), { target: { value: "srv-pass" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(putMock).toHaveBeenCalledTimes(1));
    expect(putMock).toHaveBeenCalledWith("dep_1", { login: "u", password: "srv-pass" });
    expect(await screen.findByLabelText("Публичный SSH-ключ")).toHaveValue(PUBLIC_KEY);
  });

  it("настроена: приватный ключ и пароль не показываются, ротация ключа кнопкой", async () => {
    getMock.mockResolvedValue(configured());
    putMock.mockResolvedValue(configured());
    const { container } = renderPage();
    expect(await screen.findByLabelText("Публичный SSH-ключ")).toHaveValue(PUBLIC_KEY);
    expect(container.textContent ?? "").not.toMatch(/PRIVATE KEY/);
    const passwordInput = screen.getByPlaceholderText(/оставьте пустым/) as HTMLInputElement;
    expect(passwordInput.value).toBe("");
    expect(passwordInput.type).toBe("password");
    // Ничего не меняли — сохранять нечего.
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /Сгенерировать новый ключ/ }));
    await waitFor(() => expect(putMock).toHaveBeenCalledWith("dep_1", { regenerate_ssh_key: true }));
  });

  it("смена только пароля не шлёт остальные поля", async () => {
    getMock.mockResolvedValue(configured());
    putMock.mockResolvedValue(configured());
    renderPage();
    const passwordInput = (await screen.findByPlaceholderText(/оставьте пустым/)) as HTMLInputElement;
    fireEvent.change(passwordInput, { target: { value: "new-pass" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(putMock).toHaveBeenCalledWith("dep_1", { password: "new-pass" }));
  });

  it("пункт каталога виден dep_admin и testing.admin, скрыт остальным", () => {
    const items = buildAdminItems([]);
    const id = "services.testing.test_account";
    expect(visibleItems(items, persona("dep_admin")).some((i) => i.id === id)).toBe(true);
    expect(visibleItems(items, persona(null, "admin")).some((i) => i.id === id)).toBe(true);
    expect(visibleItems(items, persona(null, "guest")).some((i) => i.id === id)).toBe(false);
    expect(visibleItems(items, persona(null)).some((i) => i.id === id)).toBe(false);
  });
});
