import { useEffect, useState } from "react";

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`${path} returned ${String(response.status)}`);
  }
  return (await response.json()) as T;
}

interface Loaded<T> {
  data: T | null;
  failed: boolean;
}

/** Load a path, and again every intervalMs when given. A null path loads nothing. */
export function useApi<T>(
  path: string | null,
  intervalMs?: number,
): Loaded<T> & { reload: () => void } {
  const [loaded, setLoaded] = useState<Loaded<T>>({
    data: null,
    failed: false,
  });
  const [round, setRound] = useState(0);

  useEffect(() => {
    if (path === null) return;
    let current = true;
    const load = () => {
      getJson<T>(path)
        .then((data) => {
          if (current) setLoaded({ data, failed: false });
        })
        .catch(() => {
          if (current) setLoaded((before) => ({ ...before, failed: true }));
        });
    };
    load();
    const timer = intervalMs ? setInterval(load, intervalMs) : undefined;
    return () => {
      current = false;
      clearInterval(timer);
    };
  }, [path, intervalMs, round]);

  return {
    ...loaded,
    reload: () => {
      setRound((n) => n + 1);
    },
  };
}
