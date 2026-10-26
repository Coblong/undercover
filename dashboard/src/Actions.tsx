import { useState } from "react";
import { postJson, reasonOf, useApi } from "./api";
import { money } from "./format";
import type { Handler } from "./types";

export function RunAgent({
  claimId,
  onDone,
}: Readonly<{ claimId: string; onDone: () => void }>) {
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const run = () => {
    setBusy(true);
    setFailure(null);
    postJson(`/api/claims/${claimId}/run`)
      .then(onDone)
      .catch((error: unknown) => {
        setFailure(reasonOf(error));
      })
      .finally(() => {
        setBusy(false);
      });
  };

  return (
    <div className="actions">
      <button type="button" className="primary" disabled={busy} onClick={run}>
        Run agent
      </button>
      {failure && <p role="alert">Could not start the agent: {failure}</p>}
    </div>
  );
}

export function SignOffForm({
  claimId,
  payout,
  onDone,
}: Readonly<{ claimId: string; payout: number; onDone: () => void }>) {
  const { data: handlers } = useApi<Handler[]>("/api/handlers");
  const [handlerId, setHandlerId] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const signOff = () => {
    setBusy(true);
    setFailure(null);
    postJson(`/api/claims/${claimId}/sign-off`, { handler_id: handlerId })
      .then(onDone)
      .catch((error: unknown) => {
        setFailure(reasonOf(error));
        setBusy(false);
      });
  };

  return (
    <form
      className="actions"
      aria-label="Sign-off"
      onSubmit={(event) => {
        event.preventDefault();
        signOff();
      }}
    >
      <p>The payout is over the limit. It is paid once a handler signs off.</p>
      <label>
        Handler
        <select
          value={handlerId}
          onChange={(event) => {
            setHandlerId(event.target.value);
          }}
        >
          <option value="">Choose a handler</option>
          {handlers?.map((handler) => (
            <option key={handler.handler_id} value={handler.handler_id}>
              {handler.name}
            </option>
          ))}
        </select>
      </label>
      <button
        type="submit"
        className="primary"
        disabled={busy || handlerId === ""}
      >
        Sign off and pay {money(payout)}
      </button>
      {failure && <p role="alert">Could not sign off this claim: {failure}</p>}
    </form>
  );
}
