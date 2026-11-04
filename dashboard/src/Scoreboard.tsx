import { useState } from "react";
import { POLL_MS, postJson, reasonOf, useApi } from "./api";
import { Chip } from "./Chip";
import {
  duration,
  modelCost,
  money,
  percent,
  RUN_LABEL,
  RUN_TONE,
  SET_LABEL,
  VERDICT_LABEL,
  VERDICT_TONE,
} from "./format";
import type { Evaluation, EvaluationClaim, EvaluationDetail } from "./types";

type EvalSet = Evaluation["eval_set"];

const anyRunning = (list: Evaluation[]) =>
  list.some((e) => e.state === "running") ? POLL_MS : undefined;

const whileRunning = (detail: EvaluationDetail) =>
  detail.state === "running" ? POLL_MS : undefined;

function StartForm({
  running,
  onStarted,
}: Readonly<{ running: boolean; onStarted: () => void }>) {
  const [evalSet, setEvalSet] = useState<EvalSet>("evaluation");
  const [tiny, setTiny] = useState(false);
  const [typed, setTyped] = useState("3");
  const [failure, setFailure] = useState<string | null>(null);
  const number = Number(typed);
  const valid = Number.isInteger(number) && number >= 1 && number <= 25;

  return (
    <form
      className="run-form"
      aria-label="Start an evaluation"
      onSubmit={(event) => {
        event.preventDefault();
        setFailure(null);
        postJson("/api/evaluations", {
          concurrency: number,
          eval_set: evalSet,
          tiny,
        })
          .then(onStarted)
          .catch((error: unknown) => {
            setFailure(reasonOf(error));
          });
      }}
    >
      <label>
        Set
        <select
          value={evalSet}
          onChange={(event) => {
            setEvalSet(event.target.value as EvalSet);
          }}
        >
          <option value="evaluation">
            {SET_LABEL.evaluation} (the queue&apos;s claims)
          </option>
          <option value="held_out">{SET_LABEL.held_out}</option>
        </select>
      </label>
      <label className="inline">
        <input
          type="checkbox"
          checked={tiny}
          onChange={(event) => {
            setTiny(event.target.checked);
          }}
        />
        Just the 5 tiny claims
      </label>
      <label>
        Claim agents at once
        <input
          type="number"
          min={1}
          max={25}
          value={typed}
          onChange={(event) => {
            setTyped(event.target.value);
          }}
        />
      </label>
      <button type="submit" className="primary" disabled={running || !valid}>
        Start an evaluation
      </button>
      {failure && <p role="alert">Could not start the evaluation: {failure}</p>}
    </form>
  );
}

function EvaluationsTable({
  evaluations,
  opened,
  onOpen,
}: Readonly<{
  evaluations: Evaluation[];
  opened: number | null;
  onOpen: (runId: number) => void;
}>) {
  return (
    <div className="table-scroll">
      <table aria-label="Evaluations">
        <thead>
          <tr>
            <th>Evaluation</th>
            <th>Set</th>
            <th>Claims</th>
            <th>At once</th>
            <th>Model</th>
            <th>Reasoning</th>
            <th>State</th>
            <th>Decision accuracy</th>
            <th>Accuracy</th>
            <th>Cost of mistakes</th>
            <th>Straight through</th>
            <th>Model cost per claim</th>
            <th>Time per claim</th>
            <th>Run time</th>
          </tr>
        </thead>
        <tbody>
          {evaluations.map((e) => (
            <tr key={e.run_id} aria-selected={e.run_id === opened}>
              <td>
                <button
                  type="button"
                  className="link mono"
                  onClick={() => {
                    onOpen(e.run_id);
                  }}
                >
                  {e.run_id}
                </button>
              </td>
              <td>{SET_LABEL[e.eval_set]}</td>
              <td className="mono">{e.claim_count}</td>
              <td className="mono">{e.concurrency}</td>
              <td className="mono">{e.model}</td>
              <td>{e.reasoning_effort}</td>
              <td>
                <Chip tone={RUN_TONE[e.state]} label={RUN_LABEL[e.state]} />
              </td>
              <td className="mono">{percent(e.measures.decision_accuracy)}</td>
              <td className="mono">{percent(e.measures.accuracy)}</td>
              <td className="mono">{money(e.measures.cost_of_mistakes)}</td>
              <td className="mono">{percent(e.measures.straight_through)}</td>
              <td className="mono">{modelCost(e.measures.cost_per_claim)}</td>
              <td className="mono">{duration(e.measures.seconds_per_claim)}</td>
              <td className="mono">
                {e.measures.run_seconds === null
                  ? "-"
                  : duration(e.measures.run_seconds)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Answer({
  decision,
  payout,
}: Readonly<{
  decision: EvaluationClaim["handler_decision"] | null;
  payout: number | null;
}>) {
  if (!decision) return <span className="muted">Not decided</span>;
  return (
    <span>
      <Chip tone={VERDICT_TONE[decision]} label={VERDICT_LABEL[decision]} />
      {decision === "approve" && payout !== null && (
        <span className="mono"> {money(payout)}</span>
      )}
    </span>
  );
}

function ClaimsTable({
  claims,
  onOpenClaim,
}: Readonly<{
  claims: EvaluationClaim[];
  onOpenClaim: (claimId: string) => void;
}>) {
  return (
    <div className="table-scroll">
      <table aria-label="Evaluation claims">
        <thead>
          <tr>
            <th>Claim</th>
            <th>Made from</th>
            <th>Handler</th>
            <th>Agent</th>
            <th>Result</th>
            <th>Cost of the mistake</th>
            <th>Model cost</th>
            <th>Time</th>
          </tr>
        </thead>
        <tbody>
          {claims.map((claim) => (
            <tr key={claim.claim_id} className={claim.right ? "" : "miss"}>
              <td>
                <button
                  type="button"
                  className="link mono"
                  onClick={() => {
                    onOpenClaim(claim.claim_id);
                  }}
                >
                  {claim.claim_id}
                </button>
              </td>
              <td className="mono">{claim.source_id}</td>
              <td>
                <Answer
                  decision={claim.handler_decision}
                  payout={claim.handler_payout}
                />
              </td>
              <td>
                <Answer
                  decision={claim.agent_decision}
                  payout={claim.agent_payout}
                />
              </td>
              <td>
                <Chip
                  tone={claim.right ? "seagreen" : "raspberry"}
                  label={claim.right ? "Match" : "Miss"}
                />
              </td>
              <td className="mono">{money(claim.mistake_cost)}</td>
              <td className="mono">{modelCost(claim.cost)}</td>
              <td className="mono">{duration(claim.seconds)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Opened({
  runId,
  onOpenClaim,
}: Readonly<{ runId: number; onOpenClaim: (claimId: string) => void }>) {
  const { data, failed } = useApi<EvaluationDetail>(
    `/api/evaluations/${String(runId)}`,
    whileRunning,
  );
  if (!data) {
    return (
      <p role={failed ? "alert" : "status"}>
        {failed ? "Could not load this evaluation." : "Loading evaluation..."}
      </p>
    );
  }
  return (
    <section className="inset" aria-label={`Evaluation ${String(runId)}`}>
      <h3>
        Evaluation <span className="mono">{runId}</span>:{" "}
        {SET_LABEL[data.eval_set]}, {data.claim_count} claims
      </h3>
      <p className="muted">
        The handler&apos;s decision beside the agent&apos;s. A miss is a
        different decision, or a payout more than a dollar off.
      </p>
      <ClaimsTable claims={data.results} onOpenClaim={onOpenClaim} />
    </section>
  );
}

export function Scoreboard({
  onOpenClaim,
}: Readonly<{ onOpenClaim: (claimId: string) => void }>) {
  const {
    data: evaluations,
    failed,
    reload,
  } = useApi<Evaluation[]>("/api/evaluations", anyRunning);
  const [opened, setOpened] = useState<number | null>(null);
  const running = evaluations?.some((e) => e.state === "running") ?? false;

  return (
    <section className="panel" aria-label="Scoreboard">
      <h2>Scoreboard</h2>
      <p className="muted">
        How the agent&apos;s decisions compare with the handlers&apos; own, on
        copies of the labelled claims.
      </p>
      <StartForm running={running} onStarted={reload} />
      {failed && <p role="alert">Could not load the evaluations.</p>}
      {evaluations?.length === 0 && (
        <p className="muted">No evaluations yet.</p>
      )}
      {evaluations && evaluations.length > 0 && (
        <EvaluationsTable
          evaluations={evaluations}
          opened={opened}
          onOpen={setOpened}
        />
      )}
      {opened !== null && (
        <Opened key={opened} runId={opened} onOpenClaim={onOpenClaim} />
      )}
    </section>
  );
}
