"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type ApiState<T> = {
  data: T;
  loading: boolean;
  /** Non-null when the last attempt failed. Callers MUST render something for
   *  this case -- see the note below on why. */
  error: string | null;
  /** Re-runs the fetch. Wired to a visible "Try again" button everywhere this
   *  hook is used. */
  retry: () => void;
};

/**
 * Fetch-on-mount for a client component, re-running when `deps` change.
 *
 * The `error` field is the point of this hook. The previous version returned
 * only {data, loading} and the fetch layer swallowed failures into an empty
 * fallback -- so a failed request was indistinguishable from a successful
 * empty one, and the landing-page graph rendered a blank canvas with no
 * explanation and no way to recover. Failure is now a first-class state that
 * callers are forced to handle.
 *
 * `initial` is still the shape rendered while loading and after an error, so
 * consumers never deal with null.
 */
export function useApi<T>(
  fetcher: () => Promise<T>,
  initial: T,
  deps: unknown[] = [],
): ApiState<T> {
  const [data, setData] = useState<T>(initial);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  // Kept in a ref so `deps` alone controls re-fetching. Callers pass inline
  // arrow functions, which are a new identity every render -- including the
  // fetcher in the effect's dependency array would loop forever.
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    fetcherRef
      .current()
      .then((result) => {
        if (cancelled) return;
        setData(result);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Something went wrong.");
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  return { data, loading, error, retry };
}
