import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";

const fetchBuildShaMock = vi.fn();
vi.mock("@/lib/appVersion", () => ({
  fetchBuildSha: (...args: unknown[]) => fetchBuildShaMock(...args),
  VERSION_POLL_INTERVAL_MS: 5000,
}));

import { UpdateBanner } from "@/components/UpdateBanner";

const reloadMock = vi.fn();

// advanceTimersByTimeAsync сам не гарантирует, что промис из fetchBuildSha()
// долетит до setState раньше, чем act(...) закончится — добиваем лишним
// микротаском, чтобы react-testing-library не ругался на update вне act.
async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("UpdateBanner", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    fetchBuildShaMock.mockReset();
    reloadMock.mockReset();
    vi.stubGlobal("location", { ...window.location, reload: reloadMock });
  });

  afterEach(() => {
    // На случай незавершённого интервала — mockResolvedValue ниже всегда
    // задаёт постоянное значение по умолчанию, так что лишний тик здесь не упадёт.
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("ничего не рендерит, пока SHA не разошёлся", async () => {
    fetchBuildShaMock.mockResolvedValue("sha-1");
    render(<UpdateBanner />);
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
    expect(screen.queryByText(/Доступна новая версия/)).not.toBeInTheDocument();

    await advance(5000);
    expect(screen.queryByText(/Доступна новая версия/)).not.toBeInTheDocument();
  });

  it("показывает баннер, когда следующий опрос возвращает другой SHA", async () => {
    fetchBuildShaMock.mockResolvedValue("sha-1");
    render(<UpdateBanner />);
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });

    fetchBuildShaMock.mockResolvedValue("sha-2");
    await advance(5000);

    expect(screen.getByText(/Доступна новая версия/)).toBeInTheDocument();
  });

  it("кнопка «Обновить» перезагружает страницу", async () => {
    fetchBuildShaMock.mockResolvedValue("sha-1");
    render(<UpdateBanner />);
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
    fetchBuildShaMock.mockResolvedValue("sha-2");
    await advance(5000);

    fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
    expect(reloadMock).toHaveBeenCalledTimes(1);
  });

  it("крестик скрывает баннер и опрос больше его не показывает", async () => {
    fetchBuildShaMock.mockResolvedValue("sha-1");
    render(<UpdateBanner />);
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
    fetchBuildShaMock.mockResolvedValue("sha-2");
    await advance(5000);
    expect(screen.getByText(/Доступна новая версия/)).toBeInTheDocument();

    fireEvent.click(screen.getByTitle("Скрыть"));
    expect(screen.queryByText(/Доступна новая версия/)).not.toBeInTheDocument();

    fetchBuildShaMock.mockResolvedValue("sha-3");
    await advance(5000);
    expect(screen.queryByText(/Доступна новая версия/)).not.toBeInTheDocument();
  });

  it("не падает и не показывает баннер, если /version.json недоступен (dev-режим)", async () => {
    fetchBuildShaMock.mockResolvedValue(null);
    render(<UpdateBanner />);
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
    await advance(20_000);
    expect(screen.queryByText(/Доступна новая версия/)).not.toBeInTheDocument();
  });
});
