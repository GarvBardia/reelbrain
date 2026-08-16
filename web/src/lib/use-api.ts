"use client";

import { useEffect, useState } from "react";

/**
 * Fetch-on-mount for a client component, re-running when `deps` change.
 * Small enough to inline everywhere, but every data page needs the exact
 * same {data, loading} shape, so it is written once here instead of five
 * times slightly differently.
 */
export function useApi<T>(fetcher: () => Promise<T>, initial: T, deps: unknown[] = []): {
  data: T;
  loading: boolean;
} {
  const [data, setData] = useState<T>(initial);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetcher().then((result) => {
      if (!cancelled) {
        setData(result);
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, loading };
}
