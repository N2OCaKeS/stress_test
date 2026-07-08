import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { ToastProvider } from "@/contexts/ToastContext";
import { ConfirmProvider } from "@/components/ui/ConfirmDialog";
import { Server } from "@/pages/server/Server";

function renderServer() {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <ToastProvider>
          <ConfirmProvider>
            <MemoryRouter initialEntries={["/server"]}>
              <Server />
            </MemoryRouter>
          </ConfirmProvider>
        </ToastProvider>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

describe("Server list — VM group (mock mode)", () => {
  it("показывает раскрытую группу ВМ по умолчанию; тумблер сворачивает её", async () => {
    renderServer();
    // Группа ВМ есть и по умолчанию развёрнута — имена ВМ сразу видны.
    const header = await screen.findByText(/Виртуальные машины ·/);
    expect(header).toBeInTheDocument();
    expect(await screen.findByText("alse-1.8-rc")).toBeInTheDocument();
    expect(screen.getAllByText("ВМ").length).toBeGreaterThan(0);
    // Клик по заголовку сворачивает группу — имена ВМ исчезают.
    fireEvent.click(header);
    expect(screen.queryByText("alse-1.8-rc")).not.toBeInTheDocument();
  });
});
