import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { TestingLogs } from "../logs";
const list = vi.hoisted(() => vi.fn());
vi.mock("@/api/testing/queueItems", () => ({ listQueueItems: list }));
vi.mock("@/api/testing/testStands", () => ({ listTestStands: async () => ({ items: [] }) }));
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
    fireEvent.change(screen.getByLabelText("Тест (код или название)"), { target: { value: "FS" } });
    fireEvent.change(screen.getByLabelText("Стенд"), { target: { value: "s1" } });
    fireEvent.change(screen.getByLabelText("ID прогона"), { target: { value: "run1" } });
    fireEvent.change(screen.getByLabelText("Создана с (MSK)"), { target: { value: "2026-09-13T03:00" } });
    fireEvent.change(screen.getByLabelText("Режим запуска"), { target: { value: "false" } });
    fireEvent.click(screen.getByRole("button", { name: "Применить фильтры" }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ kind: "campaign", q: "FS", stand_id: "s1", test_run_id: "run1", created_from: "2026-09-13T03:00:00+03:00", debug_mode: false, offset: 0 })));
    await waitFor(() => expect(screen.getByRole("button", { name: "Следующая страница" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Следующая страница" }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50, q: "FS", test_run_id: "run1" })));
  });
  it("при переходе к одиночным сбрасывает кампанию и страницу", async () => {
    renderPage("/testing/logs?kind=campaign&test_run_id=run1&offset=50");
    await screen.findByText("viewer:qi_1:running");
    fireEvent.click(screen.getByRole("button", { name: "Логи одиночных запусков" }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith({ kind: "standalone", offset: 0, limit: 50 }));
    expect(screen.queryByLabelText("ID прогона")).not.toBeInTheDocument();
  });
  it("переходит к прошлой попытке без скрывающего её фильтра", async () => {
    renderPage("/testing/logs?kind=campaign&attempt_id=qi_1&state=running");
    fireEvent.click(await screen.findByRole("button", { name: "Предыдущая попытка" }));
    await waitFor(() => expect(list).toHaveBeenLastCalledWith({ kind: "campaign", attempt_id: "qi_old", offset: 0, limit: 50 }));
  });
  it("отклоняет обратный период до обращения к API", async () => {
    renderPage();
    await screen.findByText("viewer:qi_1:running");
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
    fireEvent.click(screen.getByRole("button", { name: "Логи одиночных запусков" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("History unavailable");
    expect(screen.queryByText("viewer:qi_1:running")).not.toBeInTheDocument();
  });
});
