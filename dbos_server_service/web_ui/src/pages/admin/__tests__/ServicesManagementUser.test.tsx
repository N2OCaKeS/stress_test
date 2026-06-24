import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  within,
} from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";

const getConfigMock = vi.fn();
const putConfigMock = vi.fn();
vi.mock("@/api/server/managementUserConfig", () => ({
  get getManagementUserConfig() {
    return getConfigMock;
  },
  get putManagementUserConfig() {
    return putConfigMock;
  },
}));

import { ServicesManagementUser } from "@/pages/admin/services/ServicesManagementUser";

function makeConfig() {
  return {
    login: "dbos-mgmt",
    modes: {
      astra_orel: { groups: ["docker"], extra_create_commands: [] },
      astra_smolensk: {
        groups: [],
        extra_create_commands: ["pdpl-user -i 63 dbos-mgmt"],
      },
      astra_voronezh: { groups: [], extra_create_commands: [] },
      other_os: { groups: [], extra_create_commands: [] },
    },
  };
}

function renderPage() {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <ServicesManagementUser />
      </ToastProvider>
    </ThemeProvider>,
  );
}

describe("ServicesManagementUser", () => {
  beforeEach(() => {
    getConfigMock.mockReset();
    putConfigMock.mockReset();
    getConfigMock.mockResolvedValue(makeConfig());
    putConfigMock.mockResolvedValue(makeConfig());
  });

  it("грузит конфиг и показывает login + режимы", async () => {
    renderPage();
    expect(await screen.findByDisplayValue("dbos-mgmt")).toBeInTheDocument();
    expect(screen.getByText("Орёл")).toBeInTheDocument();
    expect(screen.getByText("Смоленск")).toBeInTheDocument();
    expect(screen.getByText("Воронеж")).toBeInTheDocument();
    expect(screen.getByText("Другая ОС")).toBeInTheDocument();
    // Команда Смоленска из конфига отрендерилась.
    expect(
      screen.getByText("pdpl-user -i 63 dbos-mgmt"),
    ).toBeInTheDocument();
  });

  it("правка команды режима и сохранение зовёт PUT с верным payload", async () => {
    renderPage();
    await screen.findByDisplayValue("dbos-mgmt");

    // Находим редактор Воронежа и добавляем команду в его список команд.
    const voronezhCard = screen.getByText("Воронеж").parentElement!;
    const cmdInput = within(voronezhCard).getByPlaceholderText(
      "pdpl-user -i 63 dbos-mgmt",
    );
    fireEvent.change(cmdInput, { target: { value: "echo hi" } });
    const addBtns = within(voronezhCard).getAllByRole("button", {
      name: /Добавить/,
    });
    fireEvent.click(addBtns[addBtns.length - 1]);

    expect(await screen.findByText("echo hi")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Сохранить/ }));

    await waitFor(() => expect(putConfigMock).toHaveBeenCalledTimes(1));
    const payload = putConfigMock.mock.calls[0][0];
    expect(payload.login).toBe("dbos-mgmt");
    expect(payload.modes.astra_voronezh.extra_create_commands).toEqual([
      "echo hi",
    ]);
    // Прочие режимы не потеряли свои значения.
    expect(payload.modes.astra_smolensk.extra_create_commands).toEqual([
      "pdpl-user -i 63 dbos-mgmt",
    ]);
    expect(payload.modes.astra_orel.groups).toEqual(["docker"]);
  });

  it("показывает предупреждение о cutover при смене login", async () => {
    renderPage();
    const loginInput = await screen.findByDisplayValue("dbos-mgmt");

    expect(screen.queryByText(/cutover/i)).not.toBeInTheDocument();

    fireEvent.change(loginInput, { target: { value: "dbos-mgmt-new" } });

    expect(await screen.findByText(/cutover/i)).toBeInTheDocument();
  });
});
