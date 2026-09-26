import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";

vi.mock("@/contexts/PersonaContext", () => ({
  usePersona: () => ({
    persona: { id: "u1", dept_id: "dep_1", platform_role: "dep_admin", service_roles: {} },
  }),
}));

const getDepartmentTestSettingsMock = vi.fn();
const upsertDepartmentTestSettingsMock = vi.fn();
vi.mock("@/api/testing/departmentTestSettings", () => ({
  getDepartmentTestSettings: (...a: unknown[]) => getDepartmentTestSettingsMock(...a),
  upsertDepartmentTestSettings: (...a: unknown[]) => upsertDepartmentTestSettingsMock(...a),
}));

const getZephyrStatusMappingMock = vi.fn();
const putZephyrStatusMappingMock = vi.fn();
const resetZephyrStatusMappingMock = vi.fn();
vi.mock("@/api/testing/zephyrStatusMappings", () => ({
  getZephyrStatusMapping: (...a: unknown[]) => getZephyrStatusMappingMock(...a),
  putZephyrStatusMapping: (...a: unknown[]) => putZephyrStatusMappingMock(...a),
  resetZephyrStatusMapping: (...a: unknown[]) => resetZephyrStatusMappingMock(...a),
}));

import { ServicesTestingQueueSettings } from "@/pages/admin/services/ServicesTestingQueueSettings";

function defaultMapping(over: Record<string, unknown> = {}) {
  return {
    department_id: "dep_1",
    is_default: true,
    items: [
      { zephyr_status: "91", outcome: "passed" },
      { zephyr_status: "92", outcome: "failed" },
      { zephyr_status: "In Progress", outcome: "not_finished" },
    ],
    ...over,
  };
}

function renderPage() {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <ServicesTestingQueueSettings />
      </ToastProvider>
    </ThemeProvider>,
  );
}

function legacyPreflight() {
  return {
    enabled: true,
    http: [
      { url: "https://jira.astralinux.ru", ok_status: "200" },
      { url: "https://releases.devos.astralinux.ru", ok_status: "200" },
    ],
    dns_hosts: ["10.177.128.198", "10.177.180.246"],
    dns_port: 53,
    poll_interval_seconds: 180,
    timeout_seconds: 7200,
    probe_timeout_seconds: 15,
  };
}

function defaults(over: Record<string, unknown> = {}) {
  return {
    id: null,
    department_id: "dep_1",
    retry_enabled: true,
    test_username: "u",
    activity_report_auto_generate: false,
    preflight: legacyPreflight(),
    zephyr_verdict_wait_seconds: 2100,
    zephyr_verdict_poll_seconds: 60,
    zephyr_verdict_unfinished_outcome: "failed",
    verdict_without_zephyr_run: "unknown",
    created_at: null,
    updated_at: null,
    ...over,
  };
}

describe("ServicesTestingQueueSettings — очередь/ретраи тестирования отдела (department_test_settings)", () => {
  beforeEach(() => {
    getDepartmentTestSettingsMock.mockReset().mockResolvedValue(defaults());
    upsertDepartmentTestSettingsMock.mockReset();
    getZephyrStatusMappingMock.mockReset().mockResolvedValue(defaultMapping());
    putZephyrStatusMappingMock.mockReset();
    resetZephyrStatusMappingMock.mockReset();
  });

  it("показывает дефолт retry_enabled, учётки здесь больше нет", async () => {
    renderPage();
    expect(await screen.findByText("Очередь и повторы тестирования")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("switch", { name: /Повторять/ })).toBeChecked());
    // Учётка исполнения теста переехала на страницу «Тестовая учётка».
    expect(screen.queryByPlaceholderText("u")).not.toBeInTheDocument();
    expect(screen.getByText(/в разделе «Тестовая учётка»/)).toBeInTheDocument();
    // Расписание отчёта переехало на страницу отчёта — здесь его больше нет.
    expect(screen.queryByText(/Расписание отчёта по активностям/)).not.toBeInTheDocument();
  });

  it("сохранение зовёт upsertDepartmentTestSettings только с retry_enabled", async () => {
    upsertDepartmentTestSettingsMock.mockResolvedValue(defaults({ retry_enabled: false }));
    renderPage();
    await screen.findByText("Очередь и повторы тестирования");
    const retrySwitch = (await screen.findByRole("switch", { name: /Повторять/ })) as HTMLInputElement;
    await waitFor(() => expect(retrySwitch.checked).toBe(true));

    fireEvent.click(retrySwitch);
    await waitFor(() => expect(retrySwitch.checked).toBe(false));

    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    await waitFor(() => expect(upsertDepartmentTestSettingsMock).toHaveBeenCalledTimes(1));
    expect(upsertDepartmentTestSettingsMock).toHaveBeenCalledWith("dep_1", {
      retry_enabled: false,
    });
  });

  it("показывает порядок прогона РЦ: легаси по умолчанию, режим → ядро → имя тест-кейса", async () => {
    getDepartmentTestSettingsMock.mockResolvedValue(defaults({
      campaign_sort_rule: [
        { key: "mode", direction: "asc" },
        { key: "kernel", direction: "asc" },
        { key: "test_case_name", direction: "asc" },
      ],
    }));
    renderPage();
    expect(await screen.findByText("Порядок прогона РЦ")).toBeInTheDocument();
    const rows = await screen.findAllByTestId("campaign-sort-rule-row");
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("Режим (orel/smolensk)"),
      expect.stringContaining("Ядро"),
      expect.stringContaining("Имя тест-кейса"),
    ]);
    expect(screen.getByRole("button", { name: /Как в легаси/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  });

  it("бэкенд без campaign_sort_rule — показывается легаси-правило, сохранение его не отправляет", async () => {
    upsertDepartmentTestSettingsMock.mockResolvedValue(defaults());
    renderPage();
    expect(await screen.findAllByTestId("campaign-sort-rule-row")).toHaveLength(3);
    const retrySwitch = screen.getByRole("switch", { name: /Повторять/ }) as HTMLInputElement;
    fireEvent.click(retrySwitch);
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(upsertDepartmentTestSettingsMock).toHaveBeenCalledTimes(1));
    expect(upsertDepartmentTestSettingsMock.mock.calls[0][1]).not.toHaveProperty("campaign_sort_rule");
  });

  it("перестановка, добавление ключа и сохранение отправляют campaign_sort_rule", async () => {
    upsertDepartmentTestSettingsMock.mockResolvedValue(defaults());
    renderPage();
    await screen.findAllByTestId("campaign-sort-rule-row");

    fireEvent.click(screen.getByRole("button", { name: "Опустить ключ 1" }));
    fireEvent.click(screen.getByRole("button", { name: /Добавить ключ/ }));
    fireEvent.click(screen.getByRole("button", { name: "Убрать ключ 3" }));

    const rows = screen.getAllByTestId("campaign-sort-rule-row");
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("Ядро"),
      expect.stringContaining("Режим (orel/smolensk)"),
      expect.stringContaining("Код теста"),
    ]);
    expect(screen.getByRole("button", { name: /Как в легаси/ })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(upsertDepartmentTestSettingsMock).toHaveBeenCalledTimes(1));
    expect(upsertDepartmentTestSettingsMock).toHaveBeenCalledWith("dep_1", {
      retry_enabled: true,
      campaign_sort_rule: [
        { key: "kernel", direction: "asc" },
        { key: "mode", direction: "asc" },
        { key: "test_code", direction: "asc" },
      ],
    });
  });

  it("«Как в легаси» возвращает дефолтное правило", async () => {
    getDepartmentTestSettingsMock.mockResolvedValue(defaults({
      campaign_sort_rule: [{ key: "priority", direction: "desc" }],
    }));
    renderPage();
    await waitFor(() => expect(screen.getAllByTestId("campaign-sort-rule-row")).toHaveLength(1));
    expect(screen.getByRole("button", { name: "Убрать ключ 1" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /Как в легаси/ }));
    expect(screen.getAllByTestId("campaign-sort-rule-row")).toHaveLength(3);
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeEnabled();
  });

  it("блок preflight показывает настройки отдела", async () => {
    renderPage();
    expect(await screen.findByText("Проверка внешних сервисов перед запуском")).toBeInTheDocument();
    expect(screen.getByLabelText("URL 1")).toHaveValue("https://jira.astralinux.ru");
    expect(screen.getByLabelText("URL 2")).toHaveValue("https://releases.devos.astralinux.ru");
    expect(screen.getByLabelText("Общий таймаут ожидания, с")).toHaveValue("7200");
    expect(screen.getByLabelText("Интервал повтора, с")).toHaveValue("180");
    expect(screen.getByRole("switch", { name: /Проверять внешние сервисы/ })).toBeChecked();
    expect(screen.getAllByText("Строго 200 (как в легаси)")).toHaveLength(2);
    // Без изменений сохранять нечего.
    expect(screen.getByRole("button", { name: "Сохранить проверку сервисов" })).toBeDisabled();
  });

  it("смена URL и таймаута уходит в PUT целым объектом preflight", async () => {
    upsertDepartmentTestSettingsMock.mockResolvedValue(defaults());
    renderPage();
    const url2 = (await screen.findByLabelText("URL 2")) as HTMLInputElement;

    fireEvent.change(url2, { target: { value: "https://mirror.example.test" } });
    fireEvent.change(screen.getByLabelText("Общий таймаут ожидания, с"), { target: { value: "600" } });
    fireEvent.change(screen.getByLabelText("DNS-серверы (по одному в строке; достаточно любого одного)"), {
      target: { value: "10.0.0.1\n 10.0.0.2 \n" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Удалить URL 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить проверку сервисов" }));

    await waitFor(() => expect(upsertDepartmentTestSettingsMock).toHaveBeenCalledTimes(1));
    expect(upsertDepartmentTestSettingsMock).toHaveBeenCalledWith("dep_1", {
      preflight: {
        enabled: true,
        http: [{ url: "https://mirror.example.test", ok_status: "200" }],
        dns_hosts: ["10.0.0.1", "10.0.0.2"],
        dns_port: 53,
        poll_interval_seconds: 180,
        timeout_seconds: 600,
        probe_timeout_seconds: 15,
      },
    });
  });

  it("добавленный адрес по умолчанию проверяется строго на 200", async () => {
    upsertDepartmentTestSettingsMock.mockResolvedValue(defaults());
    renderPage();
    await screen.findByLabelText("URL 2");
    fireEvent.click(screen.getByRole("button", { name: /Добавить адрес/ }));
    fireEvent.change(screen.getByLabelText("URL 3"), { target: { value: "https://git.example.test" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить проверку сервисов" }));

    await waitFor(() => expect(upsertDepartmentTestSettingsMock).toHaveBeenCalledTimes(1));
    const body = upsertDepartmentTestSettingsMock.mock.calls[0][1] as { preflight: { http: unknown[] } };
    expect(body.preflight.http[2]).toEqual({ url: "https://git.example.test", ok_status: "200" });
  });

  it("не сохраняет некорректный URL", async () => {
    renderPage();
    const url1 = await screen.findByLabelText("URL 1");
    fireEvent.change(url1, { target: { value: "jira.astralinux.ru" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить проверку сервисов" }));

    expect(await screen.findByText(/Некорректный URL: jira\.astralinux\.ru/)).toBeInTheDocument();
    expect(upsertDepartmentTestSettingsMock).not.toHaveBeenCalled();
  });

  it("сброс отправляет preflight: null", async () => {
    upsertDepartmentTestSettingsMock.mockResolvedValue(defaults());
    renderPage();
    await screen.findByLabelText("URL 1");
    fireEvent.click(screen.getByRole("button", { name: "Сбросить к значениям по умолчанию" }));

    await waitFor(() => expect(upsertDepartmentTestSettingsMock).toHaveBeenCalledWith("dep_1", { preflight: null }));
  });

  it("вердикт из Zephyr: дефолты T3 и сохранение ожидания отдельной кнопкой", async () => {
    upsertDepartmentTestSettingsMock.mockResolvedValue(defaults({ zephyr_verdict_wait_seconds: 1800 }));
    renderPage();
    expect(await screen.findByText("Вердикт теста из Zephyr")).toBeInTheDocument();
    const wait = screen.getByLabelText("Ждать итогового статуса, с") as HTMLInputElement;
    expect(wait.value).toBe("2100");
    expect((screen.getByLabelText("Опрашивать Zephyr раз в, с") as HTMLInputElement).value).toBe("60");
    expect(screen.getByRole("button", { name: "Сохранить ожидание" })).toBeDisabled();

    fireEvent.change(wait, { target: { value: "1800" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить ожидание" }));

    await waitFor(() => expect(upsertDepartmentTestSettingsMock).toHaveBeenCalledTimes(1));
    expect(upsertDepartmentTestSettingsMock).toHaveBeenCalledWith("dep_1", {
      zephyr_verdict_wait_seconds: 1800,
      zephyr_verdict_poll_seconds: 60,
      zephyr_verdict_unfinished_outcome: "failed",
      verdict_without_zephyr_run: "unknown",
    });
  });

  it("не сохраняет слишком частый опрос Zephyr", async () => {
    renderPage();
    const poll = await screen.findByLabelText("Опрашивать Zephyr раз в, с");
    fireEvent.change(poll, { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить ожидание" }));
    expect(await screen.findByText(/Интервал опроса Zephyr/)).toBeInTheDocument();
    expect(upsertDepartmentTestSettingsMock).not.toHaveBeenCalled();
  });

  it("маппинг статусов: набор по умолчанию, добавление статуса и сохранение", async () => {
    putZephyrStatusMappingMock.mockResolvedValue(defaultMapping({ is_default: false }));
    renderPage();
    const rows = await screen.findAllByTestId("zephyr-mapping-row");
    expect(rows).toHaveLength(3);
    expect(screen.getByText(/Сейчас действует набор по умолчанию/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сбросить к набору по умолчанию" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /Добавить статус/ }));
    fireEvent.change(screen.getByLabelText("Статус Zephyr 4"), { target: { value: " Blocked " } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить маппинг" }));

    await waitFor(() => expect(putZephyrStatusMappingMock).toHaveBeenCalledTimes(1));
    expect(putZephyrStatusMappingMock).toHaveBeenCalledWith("dep_1", [
      { zephyr_status: "91", outcome: "passed" },
      { zephyr_status: "92", outcome: "failed" },
      { zephyr_status: "In Progress", outcome: "not_finished" },
      { zephyr_status: "Blocked", outcome: "failed" },
    ]);
  });

  it("маппинг статусов: дубль без учёта регистра не отправляется", async () => {
    renderPage();
    await screen.findAllByTestId("zephyr-mapping-row");
    fireEvent.click(screen.getByRole("button", { name: /Добавить статус/ }));
    fireEvent.change(screen.getByLabelText("Статус Zephyr 4"), { target: { value: "in progress" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить маппинг" }));
    expect(await screen.findByText(/указан дважды/)).toBeInTheDocument();
    expect(putZephyrStatusMappingMock).not.toHaveBeenCalled();
  });

  it("маппинг статусов: свой набор отдела сбрасывается к умолчанию", async () => {
    getZephyrStatusMappingMock.mockResolvedValue(defaultMapping({ is_default: false }));
    resetZephyrStatusMappingMock.mockResolvedValue(defaultMapping());
    renderPage();
    await screen.findByText(/У отдела свой набор/);
    fireEvent.click(screen.getByRole("button", { name: "Сбросить к набору по умолчанию" }));
    await waitFor(() => expect(resetZephyrStatusMappingMock).toHaveBeenCalledWith("dep_1"));
  });
});
