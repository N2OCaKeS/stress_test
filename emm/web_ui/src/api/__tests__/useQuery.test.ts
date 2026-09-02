import { renderHook, waitFor } from "@testing-library/react";
import { act } from "react";
import { describe, it, expect, vi } from "vitest";
import { useQuery } from "@/api/auth/useQuery";

describe("useQuery", () => {
  it("первичная загрузка: loading=true, data=undefined до завершения", async () => {
    let resolve!: (v: string[]) => void;
    const fn = vi.fn(() => new Promise<string[]>((r) => { resolve = r; }));

    const { result } = renderHook(() => useQuery(fn, []));

    expect(result.current.loading).toBe(true);
    expect(result.current.isFetching).toBe(true);
    expect(result.current.data).toBeUndefined();

    act(() => resolve(["a", "b"]));
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.isFetching).toBe(false);
    expect(result.current.data).toEqual(["a", "b"]);
  });

  it("рефетч не поднимает loading при наличии данных", async () => {
    let calls = 0;
    const fn = vi.fn(() => Promise.resolve(["data-" + ++calls]));

    const { result } = renderHook(() => useQuery(fn, []));
    await waitFor(() => expect(result.current.data).toBeDefined());

    expect(result.current.loading).toBe(false);

    let resolveRefetch!: (v: string[]) => void;
    fn.mockImplementationOnce(() => new Promise((r) => { resolveRefetch = r; }));

    act(() => result.current.refetch());

    await waitFor(() => expect(result.current.isFetching).toBe(true));
    expect(result.current.loading).toBe(false);
    expect(result.current.data).toEqual(["data-1"]);

    act(() => resolveRefetch(["data-2"]));
    await waitFor(() => expect(result.current.isFetching).toBe(false));
    expect(result.current.data).toEqual(["data-2"]);
  });

  it("enabled=false: не загружает, loading=false", () => {
    const fn = vi.fn();
    const { result } = renderHook(() => useQuery(fn, [], { enabled: false }));
    expect(result.current.loading).toBe(false);
    expect(result.current.isFetching).toBe(false);
    expect(fn).not.toHaveBeenCalled();
  });
});
