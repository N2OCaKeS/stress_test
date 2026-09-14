import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { ConfirmProvider } from "@/components/ui/ConfirmDialog";
import { SecretLive } from "../SecretLive";

const mocks = vi.hoisted(() => ({
  list: vi.fn(), create: vi.fn(), get: vi.fn(), update: vi.fn(), reveal: vi.fn(),
  persona: { id: "u1", username: "admin", dept_id: "dep_1", platform_role: "dep_admin", service_roles: { secret: "admin" }, accessible_services: ["secret"] },
}));
vi.mock("@/contexts/PersonaContext", () => ({ usePersona: () => ({ persona: mocks.persona }) }));
vi.mock("@/components/shell/Shell", () => ({ Shell: ({ middle, children }: { middle: ReactNode; children: ReactNode }) => <>{middle}{children}</> }));
vi.mock("@/lib/labels", () => ({ useLabelMaps: () => ({ depts: new Map([["dep_1", "Испытания"]]) }), useUserLabel: () => "admin" }));
vi.mock("@/api/secret/credentials", () => ({
  listCredentials: mocks.list, createCredential: mocks.create, getCredential: mocks.get,
  updateCredential: mocks.update, revealCredential: mocks.reveal,
  deleteCredential: vi.fn(), recoverCredential: vi.fn(), transferCredential: vi.fn(),
}));

const credential = {
  id: "cred_service", name: "Jira испытаний", service: "jira", scope: "service",
  owner_user_id: null, owner_dept_id: "dep_1", login: "bot", status: "active",
  created_by: "u1", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  blocked_at: null, blocked_reason: null, visible_to_dept: false, valid_from: null, valid_to: null,
};

function page(path = "/secret/service") {
  return render(<MemoryRouter initialEntries={[path]}><ThemeProvider><ToastProvider><ConfirmProvider>
    <SecretLive />
  </ConfirmProvider></ToastProvider></ThemeProvider></MemoryRouter>);
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.persona.platform_role = "dep_admin";
  mocks.persona.service_roles.secret = "admin";
  mocks.list.mockResolvedValue({ items: [credential], next_cursor: null });
  mocks.get.mockResolvedValue(credential);
  mocks.create.mockResolvedValue(credential);
  mocks.update.mockResolvedValue(credential);
});

describe("Сервисные учётные данные", () => {
  it("фильтрует все страницы по сервисной области без раскрытия значений", async () => {
    mocks.list.mockResolvedValueOnce({ items: [credential], next_cursor: "page2" })
      .mockResolvedValueOnce({ items: [{ ...credential, id: "cred_git", name: "Git испытаний" }], next_cursor: null });
    page();
    await screen.findByText("Jira испытаний");
    fireEvent.click(await screen.findByRole("button", { name: "Загрузить ещё" }));
    await screen.findByText("Git испытаний");
    expect(mocks.list).toHaveBeenLastCalledWith(expect.objectContaining({ scope: "service", cursor: "page2" }));
    expect(mocks.reveal).not.toHaveBeenCalled();
  });

  it("создаёт сервисную запись своего отдела и сохраняет срок в UTC", async () => {
    page("/secret/service?action=new");
    fireEvent.change(screen.getByLabelText("Название *"), { target: { value: "Jira испытаний" } });
    fireEvent.change(screen.getByLabelText("Система *"), { target: { value: "jira" } });
    fireEvent.change(screen.getByLabelText("Логин"), { target: { value: "bot" } });
    fireEvent.change(screen.getByLabelText("Пароль или токен *"), { target: { value: "test-only-secret" } });
    fireEvent.change(screen.getByLabelText("valid_to (MSK, в будущем)"), { target: { value: "2099-01-01T12:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Создать" }));
    await waitFor(() => expect(mocks.create).toHaveBeenCalledWith(expect.objectContaining({
      scope: "service", owner_dept_id: "dep_1", name: "Jira испытаний", service: "jira",
      login: "bot", secret: "test-only-secret", valid_to: "2099-01-01T09:00:00.000Z",
    })));
    expect(mocks.reveal).not.toHaveBeenCalled();
  });

  it("обновляет секрет и срок через общую карточку без изменения владельца", async () => {
    page("/secret/service?id=cred_service");
    fireEvent.click(await screen.findByRole("button", { name: "Изменить" }));
    fireEvent.change(screen.getByLabelText("новый секрет (перешифровать)"), { target: { value: "replacement-test-secret" } });
    fireEvent.change(screen.getByLabelText("valid_to (MSK)"), { target: { value: "2099-02-01T12:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(mocks.update).toHaveBeenCalledWith("cred_service", {
      secret: "replacement-test-secret", valid_to: "2099-02-01T09:00:00.000Z",
    }));
    expect(mocks.reveal).not.toHaveBeenCalled();
  });

  it("не предлагает создание общих секретов оператору даже по прямой ссылке", async () => {
    mocks.persona.platform_role = "user";
    mocks.persona.service_roles.secret = "operator";
    page("/secret/service?action=new");
    await screen.findByText("Jira испытаний");
    expect(screen.queryByRole("button", { name: "Создать учётные данные" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Пароль или токен *")).not.toBeInTheDocument();
  });
});
