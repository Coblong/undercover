import type { Round } from "./types";

type Stage = Round["stage"];

const STEPS: { id: Stage; label: string }[] = [
  { id: "first_evaluations", label: "First evaluations" },
  { id: "improver_working", label: "The improver working" },
  { id: "change_waiting", label: "A change waiting for approval" },
  { id: "evaluating", label: "Evaluations" },
  { id: "verdict", label: "Verdict" },
  { id: "recipe_waiting", label: "A recipe waiting for approval" },
  { id: "finished", label: "Finished" },
];

type Status = "done" | "now" | "next";

const WORD: Record<Status, string> = {
  done: "Done",
  now: "Now",
  next: "Not yet",
};

function statusOf(position: number, current: number, round: Round): Status {
  if (round.state === "finished") return "done";
  if (round.state === "stopped") return "next";
  if (position < current) return "done";
  return position === current ? "now" : "next";
}

/** Where a round has got to, step by step. Every step is always shown, so the list holds still. */
export function Stages({ round }: Readonly<{ round: Round }>) {
  const current = STEPS.findIndex((step) => step.id === round.stage);
  return (
    <ol className="stages" aria-label="Where the round has got to">
      {STEPS.map((step, position) => {
        const status = statusOf(position, current, round);
        const count =
          status === "now" && round.of > 0
            ? ` (${String(round.done)} of ${String(round.of)})`
            : "";
        return (
          <li
            key={step.id}
            className={`stage stage-${status}`}
            aria-current={status === "now" ? "step" : undefined}
          >
            <span className="stage-mark" aria-hidden="true" />
            <span className="stage-label">
              {step.label}
              {count}
            </span>
            <span className="stage-status">{WORD[status]}</span>
          </li>
        );
      })}
    </ol>
  );
}
