import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";

const getStatisticsSettingsMock = vi.fn();
const updateStatisticsSettingsMock = vi.fn();
vi.mock("@/api/testing/statistics", () => ({
  get getStatisticsSettings() {
    return getStatisticsSettingsMock;
  },
  get updateStatisticsSettings() {
    return updateStatisticsSettingsMock;
  },
}));

import { ServicesStatisticsSettings } from "@/pages/admin/services/ServicesStatisticsSettings";

function makeSettings(over: Record<string, unknown> = {}) {
  return { enabled: false, base_url: null, ...over };
}

function renderPage() {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <ServicesStatisticsSettings />
      </ToastProvider>
    </ThemeProvider>,
  );
}

describe("ServicesStatisticsSettings", () => {
  beforeEach(() => {
    getStatisticsSettingsMock.mockReset();
    updateStatisticsSettingsMock.mockReset();
  });

  it("грузит настройки и показывает base_url", async () => {
    getStatisticsSettingsMock.mockResolvedValue(
      makeSettings({ enabled: true, base_url: "http://allta.devos.astralinux.ru:7777" }),
    );
    renderPage();
    expect(
      await screen.findByDisplayValue("http://allta.devos.astralinux.ru:7777"),
    ).toBeInTheDocument();
    expect(
      (screen.getByLabelText("Фоновый пересчёт статистики включён") as HTMLInputElement)
        .checked,
    ).toBe(true);
  });

  it("предупреждает, если включить без указанного адреса", async () => {
    getStatisticsSettingsMock.mockResolvedValue(makeSettings());
    renderPage();
    const checkbox = await screen.findByLabelText("Фоновый пересчёт статистики включён");
    fireEvent.click(checkbox);
    expect(
      await screen.findByText(/каждый триггер тихо пропустится/),
    ).toBeInTheDocument();
  });

  it("сохранение зовёт updateStatisticsSettings с текущими полями", async () => {
    getStatisticsSettingsMock.mockResolvedValue(makeSettings({ base_url: "http://old.example:7777" }));
    updateStatisticsSettingsMock.mockResolvedValue(makeSettings({ enabled: true, base_url: "http://new.example:7777" }));
    renderPage();

    const urlInput = (await screen.findByDisplayValue("http://old.example:7777")) as HTMLInputElement;
    fireEvent.change(urlInput, { target: { value: "http://new.example:7777" } });
    await waitFor(() => expect(urlInput.value).toBe("http://new.example:7777"));

    const checkbox = screen.getByLabelText("Фоновый пересчёт статистики включён") as HTMLInputElement;
    fireEvent.click(checkbox);
    await waitFor(() => expect(checkbox.checked).toBe(true));

    fireEvent.click(screen.getByRole("button", { name: /Сохранить/ }));

    await waitFor(() => expect(updateStatisticsSettingsMock).toHaveBeenCalledTimes(1));
    expect(updateStatisticsSettingsMock).toHaveBeenCalledWith({
      enabled: true,
      base_url: "http://new.example:7777",
    });
  });
});
