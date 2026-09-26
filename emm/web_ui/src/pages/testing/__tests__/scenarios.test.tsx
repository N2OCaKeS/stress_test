import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { ToastProvider } from "@/contexts/ToastContext";
import type { LaunchPreview, Scenario } from "@/api/testing/types";
import { ScenarioEditor } from "../scenarios";
import { draftToWrite, moveItem, type ScenarioDraft } from "../scenarioDraft";
import { standSetupDraft } from "../standSetupDraft";

const confirmMock = vi.fn();
vi.mock("@/components/ui/ConfirmDialog", () => ({
  useConfirm: () => ({ confirm: confirmMock }),
}));

const createScenarioMock = vi.fn();
const updateScenarioMock = vi.fn();
const deleteScenarioMock = vi.fn();
const previewScenarioMock = vi.fn();
const startScenarioRunMock = vi.fn();
const listScenarioRunsMock = vi.fn();
const stopScenarioRunMock = vi.fn();
vi.mock("@/api/testing/scenarios", () => ({
  startScenarioRun: (...args: unknown[]) => startScenarioRunMock(...args),
  listScenarioRuns: (...args: unknown[]) => listScenarioRunsMock(...args),
  stopScenarioRun: (...args: unknown[]) => stopScenarioRunMock(...args),
  listScenarios: vi.fn(async () => ({ items: [] })),
  getScenario: vi.fn(),
  createScenario: (...args: unknown[]) => createScenarioMock(...args),
  updateScenario: (...args: unknown[]) => updateScenarioMock(...args),
  deleteScenario: (...args: unknown[]) => deleteScenarioMock(...args),
  previewScenario: (...args: unknown[]) => previewScenarioMock(...args),
}));

vi.mock("@/api/testing/standCatalogue", () => ({
  standName: (s?: { id: string }) => (s ? `name-${s.id}` : "?"),
  listNamedTestStands: vi.fn(async () => [
    { id: "stand_kd", server_id: "srv_1", department_id: "dep_a", server: null },
    { id: "stand_cl", server_id: "srv_2", department_id: "dep_a", server: null, target_type: "vm" },
  ]),
}));

vi.mock("@/api/testing/testDefinitions", () => ({
  listTestDefinitions: vi.fn(async () => ({
    items: [
      { id: "td_srv", code: "freeipa.server", full_name: "IPA server" },
      { id: "td_cl", code: "freeipa.client", full_name: "IPA client" },
    ],
  })),
}));

vi.mock("@/api/testing/provisioningProfiles", () => ({
  listProvisioningProfiles: vi.fn(async () => ({ items: [] })),
}));

vi.mock("@/api/server/osVersions", () => ({
  listOsVersions: vi.fn(async () => ({ items: [{ id: "osv_1", name: "1.8.1", kernels: ["6.1.0"] }] })),
  resolveOsKernels: vi.fn(),
}));

const SCENARIO: Scenario = {
  id: "scn_1",
  code: "freeipa",
  name: "FreeIPA",
  department_id: "dep_a",
  readiness: "development",
  created_by: null,
  created_at: "2026-09-24T00:00:00Z",
  updated_at: "2026-09-24T00:00:00Z",
  stands: [
    { id: "scs_1", stand_id: "stand_kd", label: "КД", preparation: "full", provisioning_profile_id: null,
      stand_setup: null, kernel_override: null, mode_override: null, target_type: "server", stand_name: "stand3" },
    { id: "scs_2", stand_id: "stand_cl", label: "клиент", preparation: "revert_only", provisioning_profile_id: null,
      stand_setup: null, kernel_override: "5.15.0", mode_override: null, target_type: "vm", stand_name: null },
  ],
  actions: [
    { id: "a1", position: 0, kind: "run_test", stand_id: "stand_kd", test_id: "td_srv", test_code: "freeipa.server",
      is_verdict: false, params: {} },
    { id: "a2", position: 1, kind: "wait", stand_id: null, test_id: null, test_code: null, is_verdict: false,
      params: { seconds: 60 } },
    { id: "a3", position: 2, kind: "run_test", stand_id: "stand_cl", test_id: "td_cl", test_code: "freeipa.client",
      is_verdict: true, params: {} },
  ],
};

function renderEditor(scenario: Scenario | null = SCENARIO) {
  const onSaved = vi.fn();
  render(
    <ToastProvider>
      <ScenarioEditor scenario={scenario} departmentId="dep_a" onSaved={onSaved} onDeleted={vi.fn()} />
    </ToastProvider>,
  );
  return { onSaved };
}

beforeEach(() => {
  vi.clearAllMocks();
  updateScenarioMock.mockImplementation(async (_id: string, body: unknown) => ({ ...SCENARIO, ...(body as object) }));
  createScenarioMock.mockImplementation(async () => ({ ...SCENARIO, id: "scn_new" }));
  listScenarioRunsMock.mockResolvedValue({ items: [] });
});

describe("scenarioDraft", () => {
  const base: ScenarioDraft = {
    code: "x", name: "X", readiness: "development",
    stands: [{ stand_id: "s1", label: "", preparation: "full", provisioning_profile_id: "",
      setup: standSetupDraft(null), kernel_override: "", mode_override: "" }],
    actions: [{ key: "k1", kind: "run_test", stand_id: "s1", test_id: "t1", is_verdict: true, seconds: "" }],
  };

  it("draftToWrite собирает тело API", () => {
    expect(draftToWrite(base, "dep_a")).toEqual({
      code: "x", name: "X", department_id: "dep_a", readiness: "development",
      stands: [{ stand_id: "s1", label: null, preparation: "full", provisioning_profile_id: null,
        stand_setup: null, kernel_override: null, mode_override: null }],
      actions: [{ kind: "run_test", stand_id: "s1", test_id: "t1", is_verdict: true, params: {} }],
    });
  });

  it("draftToWrite ловит ошибки до отправки", () => {
    expect(draftToWrite({ ...base, actions: [] }, "dep_a")).toMatch(/хотя бы одно действие/);
    expect(draftToWrite({ ...base, actions: [{ ...base.actions[0], is_verdict: false }] }, "dep_a")).toMatch(/вердикт/);
    expect(draftToWrite({ ...base, actions: [{ ...base.actions[0], test_id: "" }] }, "dep_a")).toMatch(/выберите тест/);
    expect(draftToWrite({
      ...base, actions: [...base.actions, { key: "k2", kind: "wait", stand_id: "", test_id: "", is_verdict: false, seconds: "0" }],
    }, "dep_a")).toMatch(/ожидание/);
  });

  it("moveItem переставляет элемент", () => {
    expect(moveItem(["a", "b", "c"], 2, 0)).toEqual(["c", "a", "b"]);
    expect(moveItem(["a", "b"], 0, 5)).toEqual(["a", "b"]);
  });
});

describe("ScenarioEditor", () => {
  it("показывает стенды с целью и действия по порядку", async () => {
    renderEditor();
    const stands = await screen.findAllByTestId("scenario-stand");
    expect(stands).toHaveLength(2);
    await waitFor(() => expect(within(stands[1]).getByText("ВМ")).toBeInTheDocument());
    expect(screen.getAllByTestId("scenario-action")).toHaveLength(3);
    // Запуск — только с выбранными РЦ и ядром.
    expect(screen.getByRole("button", { name: "Запустить сценарий" })).toBeDisabled();
  });

  it("запуск: РЦ и ядро → startScenarioRun; карточка запуска показывает ожидание стендов и останавливается", async () => {
    const waiting = {
      id: "scr_1", scenario_id: "scn_1", department_id: "dep_a", state: "waiting_for_stands",
      launch_context: { RC: "osv_1", KERNEL: "6.1.0", MODE: "orel" }, debug_mode: true, current_position: null,
      wait_until: null, blocked_by: [{ stand_id: "stand_cl", reason: "stand_queue_active" }], verdict: null,
      error: null, created_by: null, created_at: "2026-09-24T00:00:00Z", started_at: null, finished_at: null,
      stands: [
        { stand_id: "stand_kd", label: "КД", preparation: "full", kernel: "6.1.0", mode: "orel", state: "pending", error: null },
        { stand_id: "stand_cl", label: "клиент", preparation: "revert_only", kernel: "5.15.0", mode: "orel", state: "pending", error: null },
      ],
      actions: SCENARIO.actions.map((a) => ({ ...a, queue_item_id: null, state: null, verdict: null })),
    };
    startScenarioRunMock.mockResolvedValue(waiting);
    stopScenarioRunMock.mockResolvedValue({ ...waiting, state: "stopped" });
    renderEditor();
    fireEvent.click(await screen.findByRole("button", { name: /Выберите РЦ для запуска/ }));
    fireEvent.click(await screen.findByRole("option", { name: "1.8.1" }));
    expect(screen.getByLabelText("Ядро запуска")).toHaveValue("6.1.0");
    listScenarioRunsMock.mockResolvedValue({ items: [waiting] });
    fireEvent.click(screen.getByRole("button", { name: "Запустить сценарий" }));
    await waitFor(() => expect(startScenarioRunMock).toHaveBeenCalledWith("scn_1", {
      os_version_id: "osv_1", kernel: "6.1.0", mode: "orel", debug: true,
    }));
    const card = await screen.findByTestId("scenario-run");
    expect(within(card).getByText("Ждёт стенды")).toBeInTheDocument();
    expect(within(card).getByText(/Заняты: клиент · name-stand_cl \(stand_queue_active\)/)).toBeInTheDocument();
    fireEvent.click(within(card).getByRole("button", { name: "Остановить сценарий" }));
    await waitFor(() => expect(stopScenarioRunMock).toHaveBeenCalledWith("scr_1"));
  });

  it("перетаскивание действия меняет порядок в сохранённом сценарии", async () => {
    renderEditor();
    const rows = await screen.findAllByTestId("scenario-action");
    fireEvent.dragStart(rows[2]);
    fireEvent.dragOver(rows[0]);
    fireEvent.drop(rows[0]);
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(updateScenarioMock).toHaveBeenCalled());
    const body = updateScenarioMock.mock.calls[0][1];
    expect(body.actions.map((a: { kind: string; test_id: string | null }) => [a.kind, a.test_id])).toEqual([
      ["run_test", "td_cl"], ["run_test", "td_srv"], ["wait", null],
    ]);
    expect(body.stands[1]).toMatchObject({ stand_id: "stand_cl", preparation: "revert_only", kernel_override: "5.15.0" });
  });

  it("стрелки тоже двигают действие", async () => {
    renderEditor();
    await screen.findAllByTestId("scenario-action");
    fireEvent.click(screen.getByRole("button", { name: "Ниже 1" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(updateScenarioMock).toHaveBeenCalled());
    expect(updateScenarioMock.mock.calls[0][1].actions.map((a: { kind: string }) => a.kind))
      .toEqual(["wait", "run_test", "run_test"]);
  });

  it("без вердикта сохранение не уходит на сервер", async () => {
    renderEditor();
    await screen.findAllByTestId("scenario-action");
    fireEvent.click(screen.getByRole("checkbox", { name: "Вердикт действия 3" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/вердикт/);
    expect(updateScenarioMock).not.toHaveBeenCalled();
  });

  it("новый сценарий: добавить стенд и действие ожидания, создать", async () => {
    const { onSaved } = renderEditor(null);
    fireEvent.change(screen.getByLabelText("Код сценария"), { target: { value: "new.scn" } });
    fireEvent.change(screen.getByLabelText("Название сценария"), { target: { value: "Новый" } });
    fireEvent.click(screen.getByRole("button", { name: /— стенд —/ }));
    fireEvent.click(await screen.findByRole("option", { name: "name-stand_kd" }));
    fireEvent.click(screen.getByRole("button", { name: "Добавить" }));
    expect(await screen.findAllByTestId("scenario-stand")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: /Прогнать тест/ }));
    fireEvent.click(screen.getByRole("button", { name: /— тест —/ }));
    fireEvent.click(await screen.findByRole("option", { name: "freeipa.client — IPA client" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Вердикт действия 1" }));
    fireEvent.click(screen.getByRole("button", { name: /Подождать/ }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(createScenarioMock).toHaveBeenCalled());
    expect(createScenarioMock.mock.calls[0][0]).toMatchObject({
      code: "new.scn", department_id: "dep_a",
      stands: [{ stand_id: "stand_kd", preparation: "full" }],
      actions: [
        { kind: "run_test", stand_id: "stand_kd", test_id: "td_cl", is_verdict: true },
        { kind: "wait", params: { seconds: 60 } },
      ],
    });
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith("scn_new"));
  });

  it("превью показывает команду каждого run_test", async () => {
    const launch = (stand: string, dates: string): LaunchPreview => ({
      test_id: "t", stand_id: stand, launch_context: { RC: "osv_1", KERNEL: "6.1.0", MODE: "orel" },
      debug: false, testenv: false, launch_profile: null, variables: [], dates_content_masked: dates,
      files: [], launch_command_masked: `bash starter.sh ${stand}`, stop_command: null, use_pty: true,
      cleanup_globs: [], stand_setup: null, provisioning: null, errors: [],
    } as unknown as LaunchPreview);
    previewScenarioMock.mockResolvedValue({
      scenario_id: "scn_1",
      actions: [
        { position: 0, kind: "run_test", stand_id: "stand_kd", test_id: "td_srv", is_verdict: false, params: {},
          launch: launch("stand_kd", "--run"), stand: null, errors: [] },
        { position: 1, kind: "wait", stand_id: null, test_id: null, is_verdict: false, params: { seconds: 60 },
          launch: null, stand: null, errors: [] },
        { position: 2, kind: "run_test", stand_id: "stand_cl", test_id: "td_cl", is_verdict: true, params: {},
          launch: launch("stand_cl", "--run 10.1.1.1"), stand: null, errors: [] },
      ],
    });
    renderEditor();
    fireEvent.click(await screen.findByRole("button", { name: /^Выберите РЦ$/ }));
    fireEvent.click(await screen.findByRole("option", { name: "1.8.1" }));
    expect(screen.getByLabelText("Ядро превью")).toHaveValue("6.1.0");
    fireEvent.click(screen.getByRole("button", { name: /Показать/ }));
    await waitFor(() => expect(previewScenarioMock).toHaveBeenCalledWith("scn_1", { os_version_id: "osv_1", kernel: "6.1.0" }));
    const blocks = await screen.findAllByTestId("scenario-preview-action");
    expect(blocks).toHaveLength(3);
    expect(within(blocks[2]).getByText("--run 10.1.1.1")).toBeInTheDocument();
    expect(within(blocks[2]).getByText("bash starter.sh stand_cl")).toBeInTheDocument();
    expect(within(blocks[1]).getByText(/60 с/)).toBeInTheDocument();
  });
});
