import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ToastProvider } from "@/contexts/ToastContext";
import type { TestDefinition, TestStep } from "@/api/testing/types";

const confirmMock = vi.fn(async () => true);
vi.mock("@/components/ui/ConfirmDialog", () => ({
  useConfirm: () => ({ confirm: confirmMock }),
}));

vi.mock("@/api/testing/provisioningProfiles", () => ({
  listProvisioningProfiles: vi.fn(async () => ({ items: [] })),
}));

const listTestStepsMock = vi.fn();
const createTestStepMock = vi.fn();
const updateTestStepMock = vi.fn();
const deleteTestStepMock = vi.fn();
const reorderTestStepsMock = vi.fn();
vi.mock("@/api/testing/testSteps", () => ({
  listTestSteps: (...args: unknown[]) => listTestStepsMock(...args),
  createTestStep: (...args: unknown[]) => createTestStepMock(...args),
  updateTestStep: (...args: unknown[]) => updateTestStepMock(...args),
  deleteTestStep: (...args: unknown[]) => deleteTestStepMock(...args),
  reorderTestSteps: (...args: unknown[]) => reorderTestStepsMock(...args),
}));

import { TestStepsPanel } from "@/pages/testing/TestStepsPanel";
import { stepSummary } from "@/pages/testing/stepLabels";
import { stepProgress, withStepProgress } from "@/pages/testing/queueStepProgress";

const TEST = {
  id: "td_k", code: "postgresql.kernels", full_name: "postgresql benchmark kernels", category: "postgresql",
  owner: null, readiness: "ready", mode: "orel", department_id: "dep_a", pinned_stand_id: null,
  changelog_component: null, timeout_seconds: null, created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z", created_by: null,
} as TestDefinition;

function step(id: string, position: number, overrides: Partial<TestStep> = {}): TestStep {
  return {
    id, test_id: "td_k", position, name: "", starter_suffix: "kernel", run_mode: position === 0 ? "full" : "rerun",
    stand_setup: null, created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:00Z", ...overrides,
  };
}

const STEPS = [
  step("s1", 0, { name: "maxcpus=8 · begin", stand_setup: {
    kernel_cmdline_extra: ["maxcpus=8"], script: "", run_as: "root", phase: "after_boot", reboot_after: null, timeout_seconds: 1800,
  } }),
  step("s2", 1, { name: "maxcpus=16" }),
];

function renderPanel(stepId: string | null = null) {
  const onStepChange = vi.fn();
  render(
    <ToastProvider>
      <TestStepsPanel test={TEST} stepId={stepId} onStepChange={onStepChange} />
    </ToastProvider>,
  );
  return onStepChange;
}

beforeEach(() => {
  vi.clearAllMocks();
  listTestStepsMock.mockResolvedValue([...STEPS]);
});

describe("TestStepsPanel", () => {
  it("показывает шаги вкладками и переключает шаг", async () => {
    const onStepChange = renderPanel();
    const second = await screen.findByRole("button", { name: "2. maxcpus=16" });
    expect(screen.getByRole("button", { name: "1. maxcpus=8 · begin" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(second);
    expect(onStepChange).toHaveBeenCalledWith("s2");
    fireEvent.click(screen.getByRole("button", { name: "1. maxcpus=8 · begin" }));
    expect(onStepChange).toHaveBeenCalledWith(null);
  });

  it("добавляет шаг копией параметров текущего и выбирает его", async () => {
    createTestStepMock.mockResolvedValue(step("s3", 2, { name: "Шаг 3" }));
    const onStepChange = renderPanel("s2");
    fireEvent.click(await screen.findByRole("button", { name: /Добавить шаг/ }));
    await waitFor(() => expect(createTestStepMock).toHaveBeenCalledWith("td_k", {
      name: "Шаг 3", run_mode: "rerun", copy_args_from_step_id: "s2",
    }));
    await waitFor(() => expect(onStepChange).toHaveBeenCalledWith("s3"));
  });

  it("сохраняет имя, суффикс и параметры ядра шага", async () => {
    updateTestStepMock.mockResolvedValue(STEPS[1]);
    renderPanel("s2");
    const name = await screen.findByLabelText("Имя шага");
    expect(name).toHaveValue("maxcpus=16");
    fireEvent.change(name, { target: { value: "maxcpus=16 фаза" } });
    fireEvent.change(screen.getByLabelText("Доп. параметры ядра"), { target: { value: "maxcpus=16" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить шаг" }));
    await waitFor(() => expect(updateTestStepMock).toHaveBeenCalledWith("td_k", "s2", {
      name: "maxcpus=16 фаза",
      run_mode: "rerun",
      starter_suffix: "kernel",
      stand_setup: {
        kernel_cmdline_extra: ["maxcpus=16"], script: "", run_as: "root", phase: "after_boot",
        reboot_after: null, timeout_seconds: 1800,
      },
    }));
  });

  it("неверный параметр ядра не даёт сохранить шаг", async () => {
    renderPanel("s2");
    fireEvent.change(await screen.findByLabelText("Доп. параметры ядра"), { target: { value: "max cpus;rm" } });
    expect(screen.getByRole("button", { name: "Сохранить шаг" })).toBeDisabled();
  });

  it("удаляет шаг после подтверждения; последний шаг удалить нельзя", async () => {
    deleteTestStepMock.mockResolvedValue({ ok: true });
    const onStepChange = renderPanel("s2");
    fireEvent.click(await screen.findByRole("button", { name: /Удалить шаг/ }));
    await waitFor(() => expect(deleteTestStepMock).toHaveBeenCalledWith("td_k", "s2"));
    expect(confirmMock).toHaveBeenCalled();
    await waitFor(() => expect(onStepChange).toHaveBeenCalledWith(null));
  });

  it("у одношагового теста удаление недоступно, есть подсказка", async () => {
    listTestStepsMock.mockResolvedValue([step("s1", 0)]);
    renderPanel();
    expect(await screen.findByText(/Одношаговый тест/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Удалить шаг/ })).toBeDisabled();
  });

  it("переставляет шаги", async () => {
    reorderTestStepsMock.mockResolvedValue([STEPS[1], STEPS[0]]);
    renderPanel("s2");
    fireEvent.click(await screen.findByRole("button", { name: "Шаг раньше" }));
    await waitFor(() => expect(reorderTestStepsMock).toHaveBeenCalledWith("td_k", ["s2", "s1"]));
  });
});

describe("шаги: подписи и прогресс", () => {
  it("кратко описывает шаг", () => {
    expect(stepSummary(STEPS[0])).toBe("полный запуск · ядро: maxcpus=8 · $5=kernel");
    expect(stepSummary(STEPS[1])).toBe("повторный запуск · $5=kernel");
  });

  it("прогресс только у многоступенчатого item'а на стенде", () => {
    expect(stepProgress({ state: "running", current_step_index: 1, step_count: 4 })).toBe("шаг 2/4");
    expect(stepProgress({ state: "running", current_step_index: 0, step_count: 1 })).toBe("");
    expect(stepProgress({ state: "queued", current_step_index: 0, step_count: 4 })).toBe("");
    expect(withStepProgress("стенд готовится к тесту", { state: "preparing", current_step_index: 2, step_count: 4 }))
      .toBe("стенд готовится к тесту · шаг 3/4");
  });
});
