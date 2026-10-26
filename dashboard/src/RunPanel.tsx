import { useState } from "react";
import { POLL_MS, postJson, useApi } from "./api";
import { Chip } from "./Chip";
import { RUN_LABEL, RUN_TONE, TODO_LABEL, TODO_TONE } from "./format";
import type { Run } from "./types";

const whileRunning = (run: Run | null) =>
  run?.state === "running" ? POLL_MS : undefined;

function RunForm({
  waiting,
  running,
  onStarted,
}: Readonly<{ waiting: number; running: boolean; onStarted: () => void }>) {
  const [typed, setTyped] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const limit = typed ?? String(waiting);
  const valid = Number.isInteger(Number(limit)) && Number(limit) >= 1;

  return (
    <form
      className="run-form"
      aria-label="Run the queue"
      onSubmit={(event) => {
        event.preventDefault();
        setFailed(false);
        postJson("/api/runs", { limit: Number(limit) })
          .then(onStarted)
          .catch(() => {
            setFailed(true);
          });
      }}
    >
      <label>
        Claims to work
        <input
          type="number"
          min={1}
          value={limit}
          onChange={(event) => {
            setTyped(event.target.value);
          }}
        />
      </label>
      <button
        type="submit"
        className="primary"
        disabled={running || waiting === 0 || !valid}
      >
        Run the queue
      </button>
      {failed && <p role="alert">Could not start the run.</p>}
    </form>
  );
}

function Plan({ run }: Readonly<{ run: Run }>) {
  return (
    <>
      <p className="verdict">
        <Chip tone={RUN_TONE[run.state]} label={RUN_LABEL[run.state]} />
        <span className="muted">
          Up to <span className="mono">{run.limit}</span> claims
        </span>
      </p>
      {run.todos.length ? (
        <ol className="todos" aria-label="To-do list">
          {run.todos.map((todo) => (
            <li key={todo.text}>
              <Chip
                tone={TODO_TONE[todo.status]}
                label={TODO_LABEL[todo.status]}
              />
              <span>{todo.text}</span>
            </li>
          ))}
        </ol>
      ) : (
        run.state === "running" && (
          <p className="muted">The agent is planning its work.</p>
        )
      )}
      {run.summary && <p>{run.summary}</p>}
    </>
  );
}

export function RunPanel({ waiting }: Readonly<{ waiting: number }>) {
  const {
    data: run,
    failed,
    reload,
  } = useApi<Run | null>("/api/runs/latest", whileRunning);
  return (
    <section className="panel run" aria-label="Run">
      <h2>Run the queue</h2>
      <RunForm
        waiting={waiting}
        running={run?.state === "running"}
        onStarted={reload}
      />
      {failed && <p role="alert">Could not load the latest run.</p>}
      {run && <Plan run={run} />}
    </section>
  );
}
