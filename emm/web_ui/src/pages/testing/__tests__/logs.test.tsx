import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { TestingLogs } from "../logs";
const list = vi.hoisted(() => vi.fn());
const retry = vi.hoisted(() => vi.fn());
vi.mock("@/api/testing/queueItems", () => ({ listQueueItems: list, retryQueueItem: retry }));
vi.mock("@/api/testing/testStands", () => ({ listTestStands: async () => ({ items: [{ id: "s1" }] }), getTestStand: async () => ({ server: { display_name: "Стенд 1" } }) }));
vi.mock("@/api/server/osVersions", () => ({ listOsVersions: async () => ({ items: [
  { id: "os_z", name: "1.7.1.44", kernels: ["6.1.10", "6.1.2"] },
  { id: "os_a", name: "1.8.1.1", kernels: ["6.6.1"] },
] }) }));
vi.mock("@/api/testing/testDefinitions", () => ({ listTestDefinitions: async () => ({ items: [
  { id: "test_a", full_name: "Ядро", code: "KERNEL" }, { id: "test_z", full_name: "Диск", code: "DISK" },
] }) }));
vi.mock("@/api/testing/testRuns", () => ({ listTestRuns: async () => ({ items: [{ id: "run1", os_version_id: "os_z", mode: "normal", created_at: "2026-09-13T00:00:00Z" }] }) }));
async function choose(label: string, option: string | RegExp) {
  fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${label}:`) }));
  fireEvent.click(await screen.findByRole("option", { name: option }));
}
function advanced() { fireEvent.click(screen.getByText("Дополнительно: период, прогон, поиск, debug")); }
vi.mock("../AttemptLogViewer", () => ({ AttemptLogViewer: ({ queueItemId, state }: { queueItemId: string; state: string }) => <div>viewer:{queueItemId}:{state}</div> }));
const item = { id: "qi_1", test_id: "t1", test_code: "FS-CHECK", stand_id: "s1", test_run_id: "run1", state: "running", debug_mode: false, created_at: "2026-09-13T00:00:00Z", retry_of_id: "qi_old", is_current: true };
function renderPage(path = "/testing/logs?kind=campaign") {
  return render(<MemoryRouter initialEntries={[path]}><TestingLogs /></MemoryRouter>);
}
beforeEach(() => { vi.clearAllMocks(); list.mockResolvedValue({ items: [item], total: 65 }); });
describe("История логов", () => {
  it("передаёт фильтры серверу и сохраняет период MSK на следующей странице", async () => {
    renderPage();
    await screen.findByText("viewer:qi_1:running");
    advanced();
    fireEvent.change(screen.getByLabelText("Поиск по названию или коду теста"), { target: { value: "FS" } });
    await choose("Стенд", "Стенд 1");
    await choose("Прогон", /1.7.1.44/);
    fireEvent.change(screen.getByLabelText("Создана с (MSK)"), { target: { value: "2026-09-13T03:00" } });
    await choose("Режим запуска", "Обычный");
    fireEvent.click(screen.getByRole("button", { name: "Применить фильтры" }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ kind: "campaign", q: "FS", stand_id: "s1", test_run_id: "run1", created_from: "2026-09-13T03:00:00+03:00", debug_mode: false, offset: 0 })));
    await waitFor(() => expect(screen.getByRole("button", { name: "Следующая страница" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Следующая страница" }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50, q: "FS", test_run_id: "run1" })));
  });
  it("при переходе к одиночным сбрасывает кампанию и страницу", async () => {
    renderPage("/testing/logs?kind=campaign&test_run_id=run1&offset=50");
    await screen.findByText("viewer:qi_1:running");
    await choose("Вид запуска", "Одиночные запуски");
    fireEvent.click(screen.getByRole("button", { name: "Применить фильтры" }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith({ kind: "standalone", offset: 0, limit: 50 }));
    expect(screen.queryByRole("button", { name: /^Прогон:/ })).not.toBeInTheDocument();
  });
  it("переходит к прошлой попытке без скрывающего её фильтра", async () => {
    renderPage("/testing/logs?kind=campaign&attempt_id=qi_1&state=running");
    fireEvent.click(await screen.findByRole("button", { name: "Предыдущая попытка" }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith({ kind: "campaign", attempt_id: "qi_old", offset: 0, limit: 50 }));
  });
  it("отклоняет обратный период до обращения к API", async () => {
    renderPage();
    await screen.findByText("viewer:qi_1:running");
    advanced();
    fireEvent.change(screen.getByLabelText("Создана с (MSK)"), { target: { value: "2026-09-13T04:00" } });
    fireEvent.change(screen.getByLabelText("Создана до (не включая, MSK)"), { target: { value: "2026-09-13T03:00" } });
    fireEvent.click(screen.getByRole("button", { name: "Применить фильтры" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Начало периода");
    expect(list).toHaveBeenCalledTimes(1);
  });
  it("после ошибки нового фильтра не показывает старый лог", async () => {
    renderPage();
    await screen.findByText("viewer:qi_1:running");
    list.mockRejectedValueOnce(new Error("History unavailable"));
    await choose("Вид запуска", "Одиночные запуски");
    fireEvent.click(screen.getByRole("button", { name: "Применить фильтры" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("History unavailable");
    expect(screen.queryByText("viewer:qi_1:running")).not.toBeInTheDocument();
  });
  it("общий список сохраняет ротированный лог и предлагает повтор", async () => {
    list.mockResolvedValue({ items: [{ ...item, state: "failed", log_status: "rotated" }], total: 1 });
    retry.mockResolvedValue({ id: "qi_new" });
    renderPage("/testing/logs");
    expect(await screen.findByText(/Лог удалён по сроку хранения/)).toBeInTheDocument();
    expect(screen.queryByText("viewer:qi_1:failed")).not.toBeInTheDocument();
    expect(list).toHaveBeenCalledWith(expect.objectContaining({ kind: "all" }));
    fireEvent.click(screen.getByRole("button", { name: "Перезапустить тест" }));
    await waitFor(() => expect(retry).toHaveBeenCalledWith("qi_1", expect.any(String)));
  });

  it("выбирает по именам, сортирует числовые версии и ограничивает ядра выбранной ОС", async () => {
    renderPage();
    await screen.findByText("viewer:qi_1:running");
    fireEvent.click(screen.getByRole("button", { name: /^Тест:/ }));
    expect(within(screen.getByRole("listbox")).getAllByRole("option").map((o) => o.textContent)).toEqual(["Диск · DISK", "Ядро · KERNEL"]);
    fireEvent.change(screen.getByPlaceholderText("Поиск…"), { target: { value: "дис" } });
    fireEvent.click(screen.getByRole("option", { name: "Диск · DISK" }));
    await choose("ОС", "1.7.1.44");
    fireEvent.click(screen.getByRole("button", { name: /^Ядро:/ }));
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual(["6.1.2", "6.1.10"]);
    fireEvent.click(screen.getByRole("option", { name: "6.1.2" }));
    fireEvent.click(screen.getByRole("button", { name: "Применить фильтры" }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ test_id: "test_z", os_version_id: "os_z", os_version_name: "1.7.1.44", kernel: "6.1.2" })));
    await choose("ОС", "1.8.1.1");
    expect(screen.getByRole("button", { name: "Ядро: Все ядра ОС" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Применить фильтры" }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ os_version_id: "os_a" })));
    expect(list.mock.lastCall?.[0]).not.toHaveProperty("kernel");
  });
  it("сворачивает фильтры без сброса выбора и не скрывает лог", async () => {
    renderPage();
    await screen.findByText("viewer:qi_1:running");
    await choose("Стенд", "Стенд 1");
    fireEvent.click(screen.getByRole("button", { name: "Свернуть фильтры" }));
    expect(screen.queryByRole("button", { name: "Применить фильтры" })).not.toBeInTheDocument();
    expect(screen.getByText("viewer:qi_1:running")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Развернуть фильтры" }));
    expect(screen.getByRole("button", { name: "Стенд: Стенд 1" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Применить фильтры" }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ stand_id: "s1" })));
  });

});
