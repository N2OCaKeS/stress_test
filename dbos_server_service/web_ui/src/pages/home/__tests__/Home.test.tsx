import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { Home } from "@/pages/home/Home";

describe("Home (reference port)", () => {
  it("renders welcome line for the default persona (alice)", () => {
    render(
      <ThemeProvider>
        <PersonaProvider>
          <MemoryRouter initialEntries={["/home"]}>
            <Home />
          </MemoryRouter>
        </PersonaProvider>
      </ThemeProvider>,
    );

    expect(screen.getByText(/Привет, alice/)).toBeInTheDocument();
    expect(screen.getByText(/Быстрые действия/)).toBeInTheDocument();
    expect(screen.getByText(/Серверы онлайн/)).toBeInTheDocument();
  });
});
