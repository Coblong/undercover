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

/** An error that carries the reason the backend gave, in its own words. */
export class ApiError extends Error {}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

async function reasonFor(response: Response): Promise<string> {
  const fallback = `The request failed (${String(response.status)})`;
  const body: unknown = await response.json().catch(() => null);
  if (!isRecord(body)) return fallback;
  const { detail } = body;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item: unknown) => (isRecord(item) ? item.msg : undefined))
      .filter((msg): msg is string => typeof msg === "string");
    if (messages.length) return messages.join(", ");
  }
  return fallback;
}

export async function postJson(path: string, body?: unknown): Promise<void> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw new ApiError(await reasonFor(response));
}

/** The words to show for a failed action: the backend's reason, when it gave one. */
export function reasonOf(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong";
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
    let held = false;
    const tick = () => {
      if (document.hidden) held = true;
      else load();
    };
    const resume = () => {
      if (held && !document.hidden) {
        held = false;
        load();
      }
    };
    document.addEventListener("visibilitychange", resume);
    const load = () => {
      getJson<T>(path)
        .then((data) => {
          if (!current) return;
          setLoaded({ data, failed: false });
          const wait = every?.(data);
          if (wait) timer = setTimeout(tick, wait);
        })
        .catch(() => {
          if (!current) return;
          setLoaded((before) => ({ ...before, failed: true }));
          if (every) timer = setTimeout(tick, POLL_MS * 2);
        });
    };
    load();
    return () => {
      current = false;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", resume);
    };
  }, [path, every, round]);

  return {
    ...loaded,
    reload: () => {
      setRound((n) => n + 1);
    },
  };
}
