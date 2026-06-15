import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ThemeProvider } from "@/contexts/ThemeContext";
import { PersonaProvider } from "@/contexts/PersonaContext";
import { Home } from "@/pages/home/Home";

function renderHome() {
  return render(
    <ThemeProvider>
      <PersonaProvider>
        <MemoryRouter initialEntries={["/home"]}>
          <Home />
        </MemoryRouter>
      </PersonaProvider>
    </ThemeProvider>,
  );
}

describe("Home dispatcher", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("renders HomeAccountAdmin for bob", () => {
    window.localStorage.setItem("dbos-persona", "bob");
    renderHome();
    expect(screen.getByText(/Привет, bob/)).toBeInTheDocument();
    expect(screen.getByText(/Платформа целиком/)).toBeInTheDocument();
  });

  it("renders HomeDepAdmin for alice (default)", () => {
    renderHome();
    expect(screen.getByText(/Привет, alice/)).toBeInTheDocument();
    expect(screen.getByText(/Departament/)).toBeInTheDocument();
  });

  it("renders HomeLoggingAdmin for carol", () => {
    window.localStorage.setItem("dbos-persona", "carol");
    renderHome();
    expect(screen.getByText(/Привет, carol/)).toBeInTheDocument();
    expect(screen.getByText(/audit-каналу платформы/)).toBeInTheDocument();
    expect(screen.getByText(/Live audit-фид/)).toBeInTheDocument();
  });

  it("renders HomeLoggingReader for dave", () => {
    window.localStorage.setItem("dbos-persona", "dave");
    renderHome();
    expect(screen.getByText(/Привет, dave/)).toBeInTheDocument();
    expect(screen.getByText(/Read-only доступ/)).toBeInTheDocument();
    expect(screen.getByText(/Что доступно/)).toBeInTheDocument();
  });

  it("renders HomeLoggingReader for erin (logging_reader_dep)", () => {
    window.localStorage.setItem("dbos-persona", "erin");
    renderHome();
    expect(screen.getByText(/Привет, erin/)).toBeInTheDocument();
    expect(screen.getByText(/Read-only доступ/)).toBeInTheDocument();
    expect(screen.getByText(/Что доступно/)).toBeInTheDocument();
  });

  // Platform roles from auth_service get persona-specific Home variants:
  // account_admin, dep_admin, logging_admin, logging_reader, and the
  // dept-scoped logging_reader_dep (reuses the logging-reader Home). Service-
  // level admins fall through to HomeDepAdmin.
});
