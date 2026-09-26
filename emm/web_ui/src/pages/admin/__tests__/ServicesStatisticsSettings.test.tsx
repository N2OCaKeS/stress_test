import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { ConfirmProvider } from "@/components/ui/ConfirmDialog";

const getStatisticsSettingsMock = vi.fn();
const updateStatisticsSettingsMock = vi.fn();
const getStatisticsCategoriesMock = vi.fn();
const createStatisticsCategoryMock = vi.fn();
const updateStatisticsCategoryMock = vi.fn();
const deleteStatisticsCategoryMock = vi.fn();
vi.mock("@/api/testing/statistics", () => ({
  get getStatisticsSettings() {
    return getStatisticsSettingsMock;
  },
  get updateStatisticsSettings() {
    return updateStatisticsSettingsMock;
  },
  get getStatisticsCategories() {
    return getStatisticsCategoriesMock;
  },
  get createStatisticsCategory() {
    return createStatisticsCategoryMock;
  },
  get updateStatisticsCategory() {
    return updateStatisticsCategoryMock;
  },
  get deleteStatisticsCategory() {
    return deleteStatisticsCategoryMock;
  },
}));

import { ServicesStatisticsSettings } from "@/pages/admin/services/ServicesStatisticsSettings";

const APACHE = {
  id: "stcat_apache", key: "apache", label: "Apache", path: "/base-statistics",
  title_statistics: "Apache", set_of_test_types: ["apache-rp"],
  comparison_list: null, comparison_kernel_list: null, enabled: true, sort_order: 10,
};

function makeSettings(over: Record<string, unknown> = {}) {
  return { enabled: false, base_url: null, ...over };
}

function renderPage() {
  return render(
    <ThemeProvider>
      <ToastProvider>
        <ConfirmProvider>
          <ServicesStatisticsSettings />
        </ConfirmProvider>
      </ToastProvider>
    </ThemeProvider>,
  );
}

describe("ServicesStatisticsSettings", () => {
  beforeEach(() => {
    getStatisticsSettingsMock.mockReset();
    updateStatisticsSettingsMock.mockReset();
    getStatisticsCategoriesMock.mockReset();
    createStatisticsCategoryMock.mockReset();
    updateStatisticsCategoryMock.mockReset();
    deleteStatisticsCategoryMock.mockReset();
    getStatisticsCategoriesMock.mockResolvedValue([APACHE]);
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

  describe("справочник семейств", () => {
    it("показывает семейства, включая выключенные, запрашивая весь справочник", async () => {
      getStatisticsSettingsMock.mockResolvedValue(makeSettings());
      getStatisticsCategoriesMock.mockResolvedValue([
        APACHE,
        { ...APACHE, id: "stcat_docker", key: "docker", label: "Docker", path: "/docker-statistics", enabled: false },
      ]);
      renderPage();
      expect(await screen.findByTestId("statistics-category-apache")).toBeInTheDocument();
      expect(screen.getByTestId("statistics-category-docker")).toHaveTextContent("выключено");
      expect(getStatisticsCategoriesMock).toHaveBeenCalledWith({ includeDisabled: true });
    });

    it("добавляет семейство: строки и запятые разбираются в списки", async () => {
      getStatisticsSettingsMock.mockResolvedValue(makeSettings());
      createStatisticsCategoryMock.mockResolvedValue({ ...APACHE, key: "docker" });
      renderPage();
      fireEvent.click(await screen.findByRole("button", { name: /Добавить семейство/ }));

      fireEvent.change(screen.getByPlaceholderText("docker"), { target: { value: "docker" } });
      fireEvent.change(screen.getAllByPlaceholderText("Docker")[0], { target: { value: "Docker" } });
      fireEvent.change(screen.getByPlaceholderText("/docker-statistics"), { target: { value: "/docker-statistics" } });
      fireEvent.change(screen.getAllByPlaceholderText("Docker")[1], { target: { value: "Docker" } });
      fireEvent.change(screen.getByLabelText(/set_of_test_types/), { target: { value: "docker-wa\n\n docker-wb " } });
      fireEvent.change(screen.getByLabelText(/comparison_list/), { target: { value: "docker-wa, docker-wb" } });
      fireEvent.click(screen.getByRole("button", { name: "Добавить" }));

      await waitFor(() => expect(createStatisticsCategoryMock).toHaveBeenCalledTimes(1));
      expect(createStatisticsCategoryMock).toHaveBeenCalledWith({
        key: "docker",
        label: "Docker",
        path: "/docker-statistics",
        title_statistics: "Docker",
        set_of_test_types: ["docker-wa", "docker-wb"],
        comparison_list: [["docker-wa", "docker-wb"]],
        comparison_kernel_list: null,
        enabled: true,
        sort_order: 0,
      });
    });

    it("не отправляет форму с кривым ключом", async () => {
      getStatisticsSettingsMock.mockResolvedValue(makeSettings());
      renderPage();
      fireEvent.click(await screen.findByRole("button", { name: /Добавить семейство/ }));
      fireEvent.change(screen.getByPlaceholderText("docker"), { target: { value: "Docker!" } });
      fireEvent.click(screen.getByRole("button", { name: "Добавить" }));
      expect(await screen.findByText(/Ключ — латиница/)).toBeInTheDocument();
      expect(createStatisticsCategoryMock).not.toHaveBeenCalled();
    });

    it("правка шлёт PATCH без key", async () => {
      getStatisticsSettingsMock.mockResolvedValue(makeSettings());
      updateStatisticsCategoryMock.mockResolvedValue({ ...APACHE, label: "Apache RP" });
      renderPage();
      fireEvent.click(await screen.findByRole("button", { name: "Изменить Apache" }));
      const form = screen.getByRole("group", { name: "Правка Apache" });
      const [label] = within(form).getAllByDisplayValue("Apache");
      fireEvent.change(label, { target: { value: "Apache RP" } });
      fireEvent.click(within(form).getByRole("button", { name: "Сохранить" }));

      await waitFor(() => expect(updateStatisticsCategoryMock).toHaveBeenCalledTimes(1));
      const [id, body] = updateStatisticsCategoryMock.mock.calls[0];
      expect(id).toBe("stcat_apache");
      expect(body).not.toHaveProperty("key");
      expect(body.label).toBe("Apache RP");
      expect(body.set_of_test_types).toEqual(["apache-rp"]);
    });
  });
});
