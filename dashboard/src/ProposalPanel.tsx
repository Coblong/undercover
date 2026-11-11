import { useState } from "react";
import { postJson, reasonOf } from "./api";
import { ConfirmDialog } from "./ConfirmDialog";
import { Diff } from "./Diff";
import type { Proposal, Round } from "./types";

type Action = "approve" | "reject";

/** The proposal a round is waiting on, with its diff and the buttons to decide. */
export function ProposalPanel({
  round,
  proposal,
  canDecide,
  onDone,
}: Readonly<{
  round: Round;
  proposal: Proposal;
  canDecide: boolean;
  onDone: () => void;
}>) {
  const [action, setAction] = useState<Action | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const isChange = proposal.kind === "change";
  const noun = isChange
    ? `version ${String(proposal.version)}`
    : `recipe version ${String(proposal.version)}`;

  const decide = () => {
    setBusy(true);
    const path = `/api/rounds/${String(round.id)}/${action ?? "approve"}`;
    postJson(path, action === "reject" ? { reason: reason.trim() } : undefined)
      .then(() => {
        setAction(null);
        setReason("");
        setFailure(null);
        onDone();
      })
      .catch((error: unknown) => {
        setAction(null);
        setFailure(`Could not record the decision: ${reasonOf(error)}`);
      })
      .finally(() => {
        setBusy(false);
      });
  };

  return (
    <section className="panel" aria-label="Waiting for approval">
      <h3>
        {isChange ? "A change" : "A new recipe"} is waiting for approval: {noun}
      </h3>
      <p>{proposal.reason}</p>
      {isChange && (
        <dl className="facts">
          <dt>Model before</dt>
          <dd className="mono">
            {proposal.previous_model} at {proposal.previous_reasoning_effort}
          </dd>
          <dt>Model after</dt>
          <dd className="mono">
            {proposal.model} at {proposal.reasoning_effort}
          </dd>
        </dl>
      )}
      <h4>
        {isChange ? "Changes to the instructions" : "Changes to the recipe"}
      </h4>
      <Diff lines={proposal.diff} />
      {canDecide ? (
        <div className="actions row">
          <button
            type="button"
            className="primary"
            onClick={() => {
              setAction("approve");
            }}
          >
            Approve
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => {
              setAction("reject");
            }}
          >
            Reject
          </button>
        </div>
      ) : null}
      {failure && <p role="alert">{failure}</p>}
      {action === "approve" && (
        <ConfirmDialog
          title={`Approve ${noun}?`}
          confirmLabel="Approve"
          busy={busy}
          onConfirm={decide}
          onCancel={() => {
            setAction(null);
          }}
        >
          <p>
            {isChange
              ? `The improver will have version ${String(proposal.version)} evaluated once on the evaluation set and twice on the held-out set. The backend keeps it only if it meets the objectives. This spends up to $${round.spending_left.toFixed(2)} more.`
              : "The improver follows this recipe in the rounds after this one."}
          </p>
        </ConfirmDialog>
      )}
      {action === "reject" && (
        <ConfirmDialog
          title={`Reject ${noun}?`}
          confirmLabel="Reject"
          busy={busy || reason.trim() === ""}
          onConfirm={decide}
          onCancel={() => {
            setAction(null);
          }}
        >
          <label className="stack-field">
            Why? The improver reads this and may propose again.
            <textarea
              rows={4}
              value={reason}
              maxLength={2000}
              onChange={(event) => {
                setReason(event.target.value);
              }}
            />
          </label>
        </ConfirmDialog>
      )}
    </section>
  );
}
