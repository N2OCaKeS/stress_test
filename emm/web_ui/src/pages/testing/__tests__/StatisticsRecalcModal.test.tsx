import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ToastProvider } from "@/contexts/ToastContext";

const triggerStatisticsRecalcMock = vi.fn();
const getStatisticsStatusMock = vi.fn();
const getStatisticsCategoriesMock = vi.fn();
vi.mock("@/api/testing/statistics", () => ({
  triggerStatisticsRecalc: (...args: unknown[]) => triggerStatisticsRecalcMock(...args),
  getStatisticsStatus: (...args: unknown[]) => getStatisticsStatusMock(...args),
  getStatisticsCategories: (...args: unknown[]) => getStatisticsCategoriesMock(...args),
}));

import { StatisticsRecalcButton } from "@/pages/testing/StatisticsRecalcModal";

const IDLE = {
  status: "idle", triggered_by: null, category: null, categories: null, test_run_id: null,
  started_at: null, finished_at: null, error: null, updated_at: null,
};

function renderButton() {
  return render(
    <ToastProvider>
      <StatisticsRecalcButton />
    </ToastProvider>,
  );
}

async function openModal() {
  renderButton();
  fireEvent.click(screen.getByRole("button", { name: /Пересчитать статистику/ }));
  return screen.findByRole("dialog");
}

beforeEach(() => {
  vi.clearAllMocks();
  getStatisticsStatusMock.mockResolvedValue(IDLE);
  triggerStatisticsRecalcMock.mockResolvedValue({ ...IDLE, status: "running" });
  getStatisticsCategoriesMock.mockResolvedValue([
    { key: "apache", label: "Apache" },
    { key: "parsec", label: "Parsec" },
    { key: "virt", label: "Qemu/KVM/Libvirt" },
  ]);
});

describe("StatisticsRecalcButton / модалка пересчёта", () => {
  it("ничего не запрашивает, пока модалка закрыта", () => {
    renderButton();
    expect(getStatisticsStatusMock).not.toHaveBeenCalled();
    expect(getStatisticsCategoriesMock).not.toHaveBeenCalled();
  });

  it("по умолчанию выбрана «Вся статистика» — пересчёт без категорий", async () => {
    await openModal();
    expect(await screen.findByLabelText("Apache")).toBeInTheDocument();
    expect((screen.getByLabelText("Вся статистика") as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Пересчитать" }));
    await waitFor(() => expect(triggerStatisticsRecalcMock).toHaveBeenCalledWith({}));
    expect(await screen.findByText(/Пересчёт всей статистики запущен в фоне/)).toBeInTheDocument();
  });

  it("выбор семейств снимает «всё» и шлёт их одним запросом в порядке справочника", async () => {
    await openModal();
    fireEvent.click(await screen.findByLabelText("Qemu/KVM/Libvirt"));
    fireEvent.click(screen.getByLabelText("Apache"));
    expect((screen.getByLabelText("Вся статистика") as HTMLInputElement).checked).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Пересчитать" }));
    await waitFor(() =>
      expect(triggerStatisticsRecalcMock).toHaveBeenCalledWith({ categories: ["apache", "virt"] }),
    );
    expect(await screen.findByText(/«Apache», «Qemu\/KVM\/Libvirt» запущен в фоне/)).toBeInTheDocument();
  });

  it("снять все семейства — кнопка неактивна, пока не выбрано «всё» или семейство", async () => {
    await openModal();
    fireEvent.click(await screen.findByLabelText("Apache"));
    fireEvent.click(screen.getByLabelText("Вся статистика"));
    // «всё» снова включено, выбор семейств сброшен
    expect((screen.getByLabelText("Apache") as HTMLInputElement).checked).toBe(false);
    fireEvent.click(screen.getByLabelText("Вся статистика"));
    expect(screen.getByRole("button", { name: "Пересчитать" })).toBeDisabled();
  });

  it("новое семейство из справочника появляется без правки кода", async () => {
    getStatisticsCategoriesMock.mockResolvedValue([
      { key: "apache", label: "Apache" },
      { key: "docker", label: "Docker" },
    ]);
    await openModal();
    fireEvent.click(await screen.findByLabelText("Docker"));
    fireEvent.click(screen.getByRole("button", { name: "Пересчитать" }));
    await waitFor(() => expect(triggerStatisticsRecalcMock).toHaveBeenCalledWith({ categories: ["docker"] }));
  });

  it("показывает статус последнего пересчёта: объём подписями, ошибку", async () => {
    getStatisticsStatusMock.mockResolvedValue({
      ...IDLE, status: "failed", triggered_by: "manual", category: "parsec",
      categories: ["apache", "parsec"], started_at: "2026-09-24T07:00:00Z",
      finished_at: "2026-09-24T07:05:00Z", error: "parsec: boom",
    });
    await openModal();
    expect(await screen.findByText("ошибка")).toBeInTheDocument();
    expect(await screen.findByText(/Объём: Apache, Parsec · вручную/)).toBeInTheDocument();
    expect(screen.getByText("parsec: boom")).toBeInTheDocument();
  });

  it("идущий пересчёт — предупреждение и текущее семейство", async () => {
    getStatisticsStatusMock.mockResolvedValue({
      ...IDLE, status: "running", triggered_by: "manual", category: "parsec",
      categories: ["apache", "parsec"], started_at: "2026-09-24T07:00:00Z",
    });
    await openModal();
    expect(await screen.findByText("выполняется")).toBeInTheDocument();
    expect(await screen.findByText("Сейчас считается: Parsec")).toBeInTheDocument();
    expect(screen.getByText(/Пересчёт уже идёт/)).toBeInTheDocument();
  });

  it("полный автопересчёт по кампании подписан как «вся статистика»", async () => {
    getStatisticsStatusMock.mockResolvedValue({
      ...IDLE, status: "succeeded", triggered_by: "test_run", test_run_id: "run_1",
      started_at: "2026-09-24T07:00:00Z", finished_at: "2026-09-24T07:05:00Z",
    });
    await openModal();
    expect(await screen.findByText(/Объём: вся статистика · по завершении прогона/)).toBeInTheDocument();
  });

  it("ошибка запуска (например, нет прав) — тостом", async () => {
    triggerStatisticsRecalcMock.mockRejectedValueOnce(new Error("нет прав"));
    await openModal();
    await screen.findByLabelText("Apache");
    fireEvent.click(screen.getByRole("button", { name: "Пересчитать" }));
    expect(await screen.findByText("нет прав")).toBeInTheDocument();
  });

  it("после запуска статус перечитывается", async () => {
    await openModal();
    await screen.findByLabelText("Apache");
    await waitFor(() => expect(getStatisticsStatusMock).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Пересчитать" }));
    await waitFor(() => expect(getStatisticsStatusMock).toHaveBeenCalledTimes(2));
  });
});
