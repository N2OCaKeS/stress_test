import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { HelpTooltip } from "@/components/ui/HelpTooltip";

describe("HelpTooltip", () => {
  it("прячет текст справки до открытия", () => {
    render(<HelpTooltip text="Пояснение к полю" />);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("раскрывает справку по клику и закрывает повторным кликом", () => {
    render(<HelpTooltip text="Пояснение к полю" />);
    const btn = screen.getByRole("button", { name: /справка/i });
    fireEvent.click(btn);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Пояснение к полю");
    fireEvent.click(btn);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("закрывается по Escape", () => {
    render(<HelpTooltip text="Пояснение к полю" />);
    fireEvent.click(screen.getByRole("button", { name: /справка/i }));
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("использует переданный aria-label", () => {
    render(<HelpTooltip text="x" label="Справка: match_service" />);
    expect(
      screen.getByRole("button", { name: "Справка: match_service" }),
    ).toBeInTheDocument();
  });
});
