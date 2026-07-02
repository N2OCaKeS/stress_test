import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

/**
 * Средняя панель сворачивается целиком: содержимое пропадает, остаётся узкая
 * полоска с кнопкой возврата. Разворот возвращает содержимое. TopBar и
 * LeftPanel тянут провайдеры/роутер — для раскладочного теста подменяем их
 * заглушками.
 */
vi.mock("../TopBar", () => ({
  TopBar: () => <div>top-bar</div>,
}));
vi.mock("../LeftPanel", () => ({
  LeftPanel: () => <div>left-panel</div>,
}));

import { Shell } from "@/components/shell/Shell";

beforeEach(() => {
  window.localStorage.clear();
});

describe("Shell middle panel collapse", () => {
  it("сворачивает и разворачивает среднюю панель", () => {
    render(
      <Shell middle={<div>MID-CONTENT</div>}>
        <div>CHILD-CONTENT</div>
      </Shell>,
    );

    // Развёрнута по умолчанию: содержимое видно, есть кнопка «свернуть».
    expect(screen.getByText("MID-CONTENT")).toBeInTheDocument();
    const collapseBtn = screen.getByRole("button", {
      name: "Collapse middle panel",
    });

    fireEvent.click(collapseBtn);

    // Свёрнута: содержимого нет, осталась кнопка разворота.
    expect(screen.queryByText("MID-CONTENT")).not.toBeInTheDocument();
    const expandBtn = screen.getByRole("button", {
      name: "Expand middle panel",
    });

    fireEvent.click(expandBtn);

    // Развернули обратно — содержимое вернулось.
    expect(screen.getByText("MID-CONTENT")).toBeInTheDocument();
  });

  it("не показывает элементы средней панели без middle-пропа", () => {
    render(
      <Shell>
        <div>CHILD-CONTENT</div>
      </Shell>,
    );
    expect(
      screen.queryByRole("button", { name: "Collapse middle panel" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Expand middle panel" }),
    ).not.toBeInTheDocument();
  });
});
