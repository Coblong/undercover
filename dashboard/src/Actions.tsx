import { useState } from "react";
import { postJson, reasonOf } from "./api";
import { money } from "./format";
import { useMe } from "./Me";

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
  const me = useMe();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const signOff = () => {
    setBusy(true);
    setFailure(null);
    postJson(`/api/claims/${claimId}/sign-off`)
      .then(onDone)
      .catch((error: unknown) => {
        setFailure(reasonOf(error));
        setBusy(false);
      });
  };

  if (me && !me.can_approve_over_limit) {
    return (
      <div className="actions" role="note" aria-label="Sign-off">
        <p>
          The payout is over the limit. The agent pays it once a handler signs
          off.
        </p>
        <p>
          {me.name} cannot sign off payouts over the limit. Ask a handler who
          can, such as a senior claims handler.
        </p>
      </div>
    );
  }

  return (
    <form
      className="actions"
      aria-label="Sign-off"
      onSubmit={(event) => {
        event.preventDefault();
        signOff();
      }}
    >
      <p>
        The payout is over the limit. The agent pays it once a handler signs
        off.
      </p>
      {me && <p className="muted">You will sign off as {me.name}.</p>}
      <button type="submit" className="primary" disabled={busy || !me}>
        Sign off {money(payout)}
      </button>
      {failure && <p role="alert">Could not sign off this claim: {failure}</p>}
    </form>
  );
}
