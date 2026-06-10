/**
 * Tiny `useQuery`-style hook for auth_service endpoint wrappers.
 *
 * Intentionally minimal: loading / error / data / refetch. No caching, no
 * stale-while-revalidate — keep the surface area small until the project
 * picks a real query library (TanStack Query is on the wish-list).
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError } from "@/api/client";

export type AuthMockMode = boolean;

/**
 * `true` when the UI is running with `VITE_USE_MOCK_AUTH=true`. Use it to
 * decide whether to call the backend or read from `src/mocks/*`.
 */
export function useMockMode(): AuthMockMode {
  return (import.meta.env.VITE_USE_MOCK_AUTH as string | undefined) === "true";
}

export interface QueryState<T> {
  data: T | undefined;
  error: ApiError | Error | null;
  loading: boolean;
  refetch: () => void;
}

/**
 * Runs `fn` on mount and whenever `deps` change.
 *
 * Aborts on unmount via a local flag (we don't pass AbortSignal to keep the
 * footprint small; backend calls are short and idempotent).
 */
export function useQuery<T>(
  fn: () => Promise<T>,
  deps: ReadonlyArray<unknown>,
  opts: { enabled?: boolean } = {},
): QueryState<T> {
  const enabled = opts.enabled !== false;
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [loading, setLoading] = useState<boolean>(enabled);
  const [tick, setTick] = useState(0);

  const refetch = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fn()
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError || err instanceof Error) setError(err);
        else setError(new Error(String(err)));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, enabled]);

  return { data, error, loading, refetch };
}
