import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { ErrorBoundary } from "@/components/ErrorBoundary";

function Boom(): never {
  throw new Error("boom-during-render");
}

describe("ErrorBoundary", () => {
  beforeEach(() => {
    // componentDidCatch logs to console.error; silence the noise but keep the
    // spy so we can assert the boundary actually caught.
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders children when nothing throws", () => {
    render(
      <ErrorBoundary>
        <div>healthy child</div>
      </ErrorBoundary>,
    );
    expect(screen.getByText("healthy child")).toBeInTheDocument();
  });

  it("shows fallback card instead of crashing when a child throws", () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Что-то сломалось")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Перезагрузить/ }),
    ).toBeInTheDocument();
  });
});
