import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { ConfirmProvider } from "@/components/ui/ConfirmDialog";
import { Testing } from "@/pages/testing/Testing";

function renderTesting(path = "/testing") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ThemeProvider>
        <ToastProvider>
          <ConfirmProvider>
            <PersonaProvider>
              <Routes>
                <Route path="/testing/:section?" element={<Testing />} />
              </Routes>
            </PersonaProvider>
          </ConfirmProvider>
        </ToastProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe("Testing shell", () => {
  it("рендерит рабочую зону (overview) по умолчанию с реальным дашбордом пула", () => {
    renderTesting();
    expect(screen.getByText("Рабочая зона тестирования")).toBeInTheDocument();
    expect(screen.getByText("Обзор пула")).toBeInTheDocument();
  });

  it("переключается на вкладку «Прогоны»", () => {
    renderTesting("/testing/runs");
    expect(screen.getAllByText("Прогоны").length).toBeGreaterThan(0);
  });

  it("переключается на вкладку «Все запуски»", () => {
    renderTesting("/testing/debug");
    expect(screen.getAllByText("Все запуски").length).toBeGreaterThan(0);
  });
});
