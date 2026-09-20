import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { ToastProvider } from "@/contexts/ToastContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import type { TestRunCreateResponse, TestRunPartialError } from "@/api/testing/types";

vi.mock("@/api/server/osVersions", () => ({
  listOsVersions: async () => ({ items: [{ id: "osv_1", name: "1.8.5", kernels: ["6.1"] }] }),
  resolveOsKernels: async () => ({ kernels: ["6.1"] }),
}));

const createTestRunMock = vi.fn();
const previewTestRunMock = vi.fn();
vi.mock("@/api/testing/testRuns", () => ({
  createTestRun: (...args: unknown[]) => createTestRunMock(...args),
  previewTestRun: (...args: unknown[]) => previewTestRunMock(...args),
}));
vi.mock("@/api/testing/stp", () => ({ addTestToStp: vi.fn() }));
vi.mock("@/lib/labels", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/labels")>();
  const names: Record<string, string> = { usr_holder1: "Иван Петров" };
  return { ...actual, useUserLabel: (id: string | null | undefined) => (id ? names[id] ?? id : "—") };
});

import { StandGroupLaunchModal } from "@/pages/testing/StandGroupLaunchModal";

function result(errors: TestRunPartialError[]): TestRunCreateResponse {
  return {
    id: "run_1", stands_without_tests: [], enqueue_errors: errors, stp_sync_errors: [],
  } as unknown as TestRunCreateResponse;
}

const busy: TestRunPartialError = {
  stand_id: "s1", test_id: "t1", error_code: "STAND_BUSY", message: "Стенд сейчас занят — запуск недоступен",
  details: { busy_state: "busy", busy_user_id: "petrov", takeover_possible: true },
};
const busyUpdating: TestRunPartialError = {
  ...busy, details: { busy_state: "updating", takeover_possible: false },
};
const queueActive: TestRunPartialError = {
  stand_id: "s1", test_id: "t1", error_code: "STAND_QUEUE_ACTIVE", message: "На стенде уже идёт очередь",
  details: { queued_count: 3, current: { queue_item_id: "qi_1", state: "running", test_code: "DB-PG-TPCC" } },
};

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

beforeEach(() => {
  vi.clearAllMocks();
  previewTestRunMock.mockResolvedValue({
    stands_without_tests: [],
    entries: [{ stand_id: "s1", test_id: "t1", test_code: "STR-1", test_name: "stress", kernel: "6.1", mode: "orel", action: "launch" }],
  });
});

afterEach(() => {
  window.localStorage.removeItem("dbos-persona");
});

async function launchGroup(standDepartmentId: string | null = "core") {
  render(
    <ToastProvider>
      <PersonaProvider>
        <StandGroupLaunchModal standId="s1" standLabel="stand15" standDepartmentId={standDepartmentId} onClose={vi.fn()} onLaunched={vi.fn()} />
      </PersonaProvider>
    </ToastProvider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Выберите РЦ" }));
  fireEvent.click(screen.getByRole("option", { name: "1.8.5" }));
  const submit = await screen.findByRole("button", { name: "Запустить группу" });
  await waitFor(() => expect(submit).toBeEnabled());
  fireEvent.click(submit);
}

describe("StandGroupLaunchModal — занятый стенд и активная очередь", () => {
  it("держатель-пользователь: вместо usr_ id в подписи имя из auth", async () => {
    const byId: TestRunPartialError = {
      ...busy, details: { busy_state: "busy", busy_user_id: "usr_holder1", busy_actor_type: "user", takeover_possible: true },
    };
    createTestRunMock.mockResolvedValueOnce(result([byId]));
    await launchGroup();
    await screen.findByText(/Стенд занят \(Иван Петров\)/);
    expect(screen.getByRole("button", { name: "Забрать стенд у Иван Петров и запустить" })).toBeInTheDocument();
    expect(screen.queryByText(/usr_holder1/)).not.toBeInTheDocument();
  });

  it("держатель-сервис без пользователя: подпись по имени сервиса", async () => {
    const bySvc: TestRunPartialError = {
      ...busy, details: { busy_state: "testing_done", busy_service_name: "testing_service", busy_actor_type: "service", takeover_possible: true },
    };
    createTestRunMock.mockResolvedValueOnce(result([bySvc]));
    await launchGroup();
    await screen.findByRole("button", { name: "Забрать стенд у testing_service и запустить" });
  });

  it("занятый стенд: админ забирает его, повтор уходит с force=true", async () => {
    createTestRunMock.mockResolvedValueOnce(result([busy])).mockResolvedValueOnce(result([]));
    await launchGroup();
    fireEvent.click(await screen.findByRole("button", { name: "Забрать стенд у petrov и запустить" }));
    await waitFor(() => expect(createTestRunMock).toHaveBeenCalledTimes(2));
    expect(createTestRunMock.mock.calls[0][0].force).toBe(false);
    expect(createTestRunMock.mock.calls[1][0]).toEqual(expect.objectContaining({ force: true, test_run_stands: ["s1"] }));
    expect(createTestRunMock.mock.calls[1][0].on_active_queue).toBeUndefined();
    expect(screen.queryByText(/провалятся|Обходит только/)).not.toBeInTheDocument();
  });

  it("стенд в обновлении: забрать нельзя, кнопки нет", async () => {
    createTestRunMock.mockResolvedValueOnce(result([busyUpdating]));
    await launchGroup();
    await screen.findByText(/Стенд забрать нельзя/);
    expect(screen.queryByRole("button", { name: /Забрать стенд/ })).not.toBeInTheDocument();
  });

  it("занятый стенд: не-админ видит только уведомление", async () => {
    window.localStorage.setItem("dbos-persona", "erin");
    createTestRunMock.mockResolvedValueOnce(result([busy]));
    await launchGroup();
    await screen.findByText(/Стенд занят \(petrov\)/);
    expect(screen.queryByRole("button", { name: /Забрать стенд/ })).not.toBeInTheDocument();
  });

  it("стенд другого отдела: ни забора, ни выбора режима очереди даже у админа", async () => {
    createTestRunMock.mockResolvedValueOnce(result([busy, queueActive]));
    await launchGroup("other");
    await screen.findByText(/Стенд занят \(petrov\)/);
    expect(screen.queryByRole("button", { name: /Забрать стенд/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Добавить в конец очереди" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Очистить очередь и запустить сразу" })).not.toBeInTheDocument();
  });

  it("активная очередь: «Добавить в конец очереди» повторяет запуск с on_active_queue=append", async () => {
    createTestRunMock.mockResolvedValueOnce(result([queueActive])).mockResolvedValueOnce(result([]));
    await launchGroup();
    const prompt = await screen.findByRole("group", { name: "Очередь стенда занята" });
    expect(within(prompt).getByText("DB-PG-TPCC")).toBeInTheDocument();
    expect(within(prompt).getByText(/в очереди ещё 3/)).toBeInTheDocument();
    fireEvent.click(within(prompt).getByRole("button", { name: "Добавить в конец очереди" }));
    await waitFor(() => expect(createTestRunMock).toHaveBeenCalledTimes(2));
    expect(createTestRunMock.mock.calls[1][0]).toEqual(expect.objectContaining({ on_active_queue: "append", force: false }));
  });

  it("активная очередь: «Очистить очередь и запустить сразу» повторяет запуск с on_active_queue=replace", async () => {
    createTestRunMock.mockResolvedValueOnce(result([queueActive])).mockResolvedValueOnce(result([]));
    await launchGroup();
    fireEvent.click(await screen.findByRole("button", { name: "Очистить очередь и запустить сразу" }));
    await waitFor(() => expect(createTestRunMock).toHaveBeenCalledTimes(2));
    expect(createTestRunMock.mock.calls[1][0]).toEqual(expect.objectContaining({ on_active_queue: "replace" }));
    expect(createTestRunMock.mock.calls[1][0].request_id).not.toBe(createTestRunMock.mock.calls[0][0].request_id);
  });
});
