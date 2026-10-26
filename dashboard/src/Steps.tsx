import { always, useApi } from "./api";
import type { Step } from "./types";

const LABEL: Record<Step["kind"], string> = {
  tool_call: "Called",
  tool_result: "Got back",
  message: "Said",
  read: "Read",
  error: "Problem",
};

export function Steps({
  claimId,
  active,
}: Readonly<{ claimId: string; active: boolean }>) {
  const { data: steps } = useApi<Step[]>(
    `/api/claims/${claimId}/steps`,
    active ? always : undefined,
  );
  if (!steps?.length && !active) return null;
  return (
    <section className="steps" aria-label="Agent steps">
      <h3>What the agent did</h3>
      {active && <p className="muted">Working on this claim now.</p>}
      <ol>
        {steps?.map((step) => (
          <li key={step.id} className={step.kind === "error" ? "problem" : ""}>
            <span className="muted">{LABEL[step.kind]}</span>
            <span className={step.kind === "message" ? "" : "mono"}>
              {step.text}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
