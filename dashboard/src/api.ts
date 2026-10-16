import { useEffect, useState } from "react";

export const POLL_MS = 2000;

export const always = () => POLL_MS;

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`${path} returned ${String(response.status)}`);
  }
  return (await response.json()) as T;
}

export async function postJson(path: string, body?: unknown): Promise<void> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`${path} returned ${String(response.status)}`);
  }
}

interface Loaded<T> {
  data: T | null;
  failed: boolean;
}

/**
 * Load a path. After each load, `every` says how long to wait before loading
 * again, or returns undefined to stop. A null path loads nothing.
 */
export function useApi<T>(
  path: string | null,
  every?: (data: T) => number | undefined,
): Loaded<T> & { reload: () => void } {
  const [loaded, setLoaded] = useState<Loaded<T>>({
    data: null,
    failed: false,
  });
  const [round, setRound] = useState(0);

  useEffect(() => {
    if (path === null) return;
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () => {
      getJson<T>(path)
        .then((data) => {
          if (!current) return;
          setLoaded({ data, failed: false });
          const wait = every?.(data);
          if (wait) timer = setTimeout(load, wait);
        })
        .catch(() => {
          if (!current) return;
          setLoaded((before) => ({ ...before, failed: true }));
          if (every) timer = setTimeout(load, POLL_MS * 2);
        });
    };
    load();
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [path, every, round]);

  return {
    ...loaded,
    reload: () => {
      setRound((n) => n + 1);
    },
  };
}
