import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { renderHook } from "@testing-library/react";
import { usePanelHeight } from "@/components/shell/usePanelWidth";
import { HeightResizeHandle } from "@/components/shell/ResizeHandle";

beforeEach(() => {
  window.localStorage.clear();
});

describe("usePanelHeight", () => {
  it("по умолчанию высота не задана (null) и заполняет место", () => {
    const { result } = renderHook(() => usePanelHeight("k", 240, 2000));
    expect(result.current[0]).toBeNull();
  });

  it("сохраняет и клампит явную высоту в localStorage", () => {
    const { result } = renderHook(() => usePanelHeight("k", 240, 2000));
    act(() => result.current[1](5000));
    expect(result.current[0]).toBe(2000);
    expect(window.localStorage.getItem("k")).toBe("2000");

    act(() => result.current[1](100));
    expect(result.current[0]).toBe(240);
  });

  it("сброс (null) убирает ключ и возвращает заполнение", () => {
    window.localStorage.setItem("k", "600");
    const { result } = renderHook(() => usePanelHeight("k", 240, 2000));
    expect(result.current[0]).toBe(600);
    act(() => result.current[1](null));
    expect(result.current[0]).toBeNull();
    expect(window.localStorage.getItem("k")).toBeNull();
  });
});

describe("HeightResizeHandle", () => {
  it("drag ↕ меняет высоту от измеренной стартовой", () => {
    const onChange = vi.fn();
    render(
      <HeightResizeHandle
        min={240}
        max={2000}
        measure={() => 400}
        onChange={onChange}
      />,
    );
    const handle = screen.getByRole("separator");
    fireEvent.mouseDown(handle, { clientY: 100 });
    fireEvent.mouseMove(window, { clientY: 160 });
    expect(onChange).toHaveBeenLastCalledWith(460);
    fireEvent.mouseUp(window);
  });

  it("двойной клик вызывает сброс", () => {
    const onReset = vi.fn();
    render(
      <HeightResizeHandle
        min={240}
        max={2000}
        measure={() => 400}
        onChange={() => {}}
        onReset={onReset}
      />,
    );
    fireEvent.doubleClick(screen.getByRole("separator"));
    expect(onReset).toHaveBeenCalledTimes(1);
  });
});
