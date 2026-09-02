import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
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

  it("shows fallback card with the error message and a reload button", () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    // Текст ошибки виден всегда, а не только в dev.
    expect(screen.getByText(/boom-during-render/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Перезагрузить/ }),
    ).toBeInTheDocument();
  });

  it("copies message + stack to the clipboard on «Скопировать ошибку»", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /Скопировать ошибку/ }),
    );

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const copied = writeText.mock.calls[0][0] as string;
    expect(copied).toContain("boom-during-render");
  });
});
