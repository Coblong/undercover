import { useState } from "react";
import { POLL_MS, postJson, reasonOf, useApi } from "./api";
import { Chip } from "./Chip";
import {
  duration,
  modelCost,
  money,
  RUN_LABEL,
  RUN_TONE,
  STATE_LABEL,
  STATE_TONE,
  timeOfDay,
  VERDICT_LABEL,
  VERDICT_TONE,
} from "./format";
import type { Dispatch, DispatchClaim, DispatchSummary } from "./types";

export const DEFAULT_AGENTS = "3";

const whileRunning = (dispatch: Dispatch | null) =>
  dispatch?.state === "running" ? POLL_MS : undefined;

function StartForm({
  running,
  onStarted,
}: Readonly<{ running: boolean; onStarted: () => void }>) {
  const [typed, setTyped] = useState(DEFAULT_AGENTS);
  const [failure, setFailure] = useState<string | null>(null);
  const number = Number(typed);
  const valid = Number.isInteger(number) && number >= 1 && number <= 25;

  return (
    <form
      className="run-form"
      aria-label="Start a dispatch"
      onSubmit={(event) => {
        event.preventDefault();
        setFailure(null);
        postJson("/api/dispatches", { concurrency: number })
          .then(onStarted)
          .catch((error: unknown) => {
            setFailure(reasonOf(error));
          });
      }}
    >
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
        Start a dispatch
      </button>
      {failure && <p role="alert">Could not start the dispatch: {failure}</p>}
    </form>
  );
}

function Verdict({ claim }: Readonly<{ claim: DispatchClaim }>) {
  if (!claim.decision) return <span className="muted">Not decided</span>;
  const label = VERDICT_LABEL[claim.decision];
  return (
    <span>
      <Chip tone={VERDICT_TONE[claim.decision]} label={label} />
      {claim.decision === "approve" && claim.payout !== null && (
        <span className="mono"> {money(claim.payout)}</span>
      )}
    </span>
  );
}

function ClaimsTable({ claims }: Readonly<{ claims: DispatchClaim[] }>) {
  return (
    <div className="table-scroll">
      <table aria-label="Dispatch claims">
        <thead>
          <tr>
            <th>Claim</th>
            <th>Where it stands</th>
            <th>Decision</th>
            <th>Started (UTC)</th>
            <th>Time</th>
            <th>Model cost</th>
          </tr>
        </thead>
        <tbody>
          {claims.map((claim) => (
            <tr key={claim.claim_id}>
              <td className="mono">{claim.claim_id}</td>
              <td>
                <Chip
                  tone={STATE_TONE[claim.state]}
                  label={STATE_LABEL[claim.state]}
                />
              </td>
              <td>
                <Verdict claim={claim} />
              </td>
              <td className="mono">
                {claim.started_at ? timeOfDay(claim.started_at) : "Not yet"}
              </td>
              <td className="mono">{duration(claim.seconds)}</td>
              <td className="mono">{modelCost(claim.cost)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Summary({ summary }: Readonly<{ summary: DispatchSummary }>) {
  const held = summary.waiting_for_sign_off;
  return (
    <div className="summary" aria-label="Summary">
      <p>
        {summary.approved} approved, {summary.declined} declined,{" "}
        {summary.referred} referred.
      </p>
      {held.length > 0 && (
        <p>
          Waiting for a sign-off:{" "}
          {held.map((h) => `${h.claim_id} ${money(h.payout)}`).join(", ")}.
        </p>
      )}
      {summary.left_for_handler.length > 0 && (
        <p>Left for a handler: {summary.left_for_handler.join(", ")}.</p>
      )}
      {summary.in_progress.length > 0 && (
        <p>Still in progress: {summary.in_progress.join(", ")}.</p>
      )}
    </div>
  );
}

function Latest({ dispatch }: Readonly<{ dispatch: Dispatch }>) {
  return (
    <>
      <p className="verdict">
        <Chip
          tone={RUN_TONE[dispatch.state]}
          label={RUN_LABEL[dispatch.state]}
        />
        <span className="muted">
          Dispatch <span className="mono">{dispatch.run_id}</span>, up to{" "}
          <span className="mono">{dispatch.concurrency}</span> claim agents at
          once, <span className="mono">{dispatch.model}</span>, reasoning{" "}
          {dispatch.reasoning_effort}
        </span>
      </p>
      <dl className="figures">
        <div>
          <dt>Model cost</dt>
          <dd className="mono">{modelCost(dispatch.cost)}</dd>
        </div>
        <div>
          <dt>Time</dt>
          <dd className="mono">{duration(dispatch.seconds)}</dd>
        </div>
        <div>
          <dt>All claims added up</dt>
          <dd className="mono">{duration(dispatch.claim_seconds)}</dd>
        </div>
      </dl>
      {dispatch.summary && <Summary summary={dispatch.summary} />}
      {dispatch.claims.length > 0 ? (
        <ClaimsTable claims={dispatch.claims} />
      ) : (
        dispatch.state === "running" && (
          <p className="muted">Choosing the claims to work.</p>
        )
      )}
    </>
  );
}

export function DispatchPanel() {
  const {
    data: dispatch,
    failed,
    reload,
  } = useApi<Dispatch | null>("/api/dispatches/latest", whileRunning);
  return (
    <section className="panel run" aria-label="Dispatch">
      <h2>Dispatch the queue</h2>
      <StartForm running={dispatch?.state === "running"} onStarted={reload} />
      {failed && <p role="alert">Could not load the latest dispatch.</p>}
      {dispatch && <Latest dispatch={dispatch} />}
    </section>
  );
}
