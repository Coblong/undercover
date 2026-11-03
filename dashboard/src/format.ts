import type { RunState, State, Verdict } from "./types";

export type Tone = "marigold" | "cerulean" | "coral" | "seagreen" | "raspberry";

export const STATES: State[] = [
  "waiting",
  "in_progress",
  "waiting_for_sign_off",
  "decided",
  "left_for_handler",
];

export const STATE_LABEL: Record<State, string> = {
  waiting: "Waiting",
  in_progress: "In progress",
  waiting_for_sign_off: "Waiting for sign-off",
  decided: "Decided",
  left_for_handler: "Left for a handler",
};

export const STATE_TONE: Record<State, Tone> = {
  waiting: "marigold",
  in_progress: "cerulean",
  waiting_for_sign_off: "coral",
  decided: "seagreen",
  left_for_handler: "raspberry",
};

export const RUN_LABEL: Record<RunState, string> = {
  running: "Running",
  finished: "Finished",
  failed: "Failed",
  stopped: "Stopped",
};

export const RUN_TONE: Record<RunState, Tone> = {
  running: "cerulean",
  finished: "seagreen",
  failed: "raspberry",
  stopped: "coral",
};

export const VERDICT_LABEL: Record<Verdict, string> = {
  approve: "Approved",
  decline: "Declined",
  refer: "Referred",
};

export const VERDICT_TONE: Record<Verdict, Tone> = {
  approve: "seagreen",
  decline: "raspberry",
  refer: "marigold",
};

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

export function money(amount: number): string {
  return usd.format(amount);
}

const day = new Intl.DateTimeFormat("en-US", {
  dateStyle: "medium",
  timeZone: "UTC",
});

export function date(iso: string): string {
  return day.format(new Date(iso.slice(0, 10) + "T00:00:00Z"));
}

/** "trip_delay" becomes "Trip delay". */
export function words(snake: string): string {
  const text = snake.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** A key for each text that stays unique when a text repeats: "a", "a (2)". */
export function uniqueKeys(texts: string[]): string[] {
  const seen = new Map<string, number>();
  return texts.map((text) => {
    const count = (seen.get(text) ?? 0) + 1;
    seen.set(text, count);
    return count === 1 ? text : `${text} (${String(count)})`;
  });
}

const small = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 4,
  maximumFractionDigits: 4,
});

/** What a model costs, in dollars to four places: a claim costs fractions of a cent. */
export function modelCost(amount: number): string {
  return small.format(amount);
}

/** "4.2 s" for under a minute, "2 min 5 s" after. */
export function duration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const whole = Math.round(seconds);
  return `${String(Math.floor(whole / 60))} min ${String(whole % 60)} s`;
}

const clock = new Intl.DateTimeFormat("en-US", {
  timeStyle: "medium",
  hour12: false,
  timeZone: "UTC",
});

/** The time of day, in UTC, of an ISO time. */
export function timeOfDay(iso: string): string {
  return clock.format(new Date(iso));
}
