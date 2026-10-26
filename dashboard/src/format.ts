import type { Run, State, TodoStatus, Verdict } from "./types";

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

export const TODO_LABEL: Record<TodoStatus, string> = {
  to_do: "To do",
  in_progress: "In progress",
  done: "Done",
  left_for_handler: "Left for a handler",
};

export const TODO_TONE: Record<TodoStatus, Tone> = {
  to_do: "marigold",
  in_progress: "cerulean",
  done: "seagreen",
  left_for_handler: "raspberry",
};

export const RUN_LABEL: Record<Run["state"], string> = {
  running: "Running",
  finished: "Finished",
  failed: "Failed",
};

export const RUN_TONE: Record<Run["state"], Tone> = {
  running: "cerulean",
  finished: "seagreen",
  failed: "raspberry",
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
