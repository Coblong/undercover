import { useState } from "react";
import { POLL_MS, postJson, reasonOf, useApi } from "./api";
import { ConfirmDialog } from "./ConfirmDialog";
import { Diff } from "./Diff";
import { dateTime, money } from "./format";
import { CANNOT_APPROVE, useMe } from "./Me";
import { PageHeader } from "./PageHeader";
import { ProposalPanel } from "./ProposalPanel";
import { RoundChip, RoundsTable } from "./RoundsTable";
import { ScoreChart } from "./ScoreChart";
import { Stages } from "./Stages";
import type { RecipeVersion, Round, SettingsView } from "./types";

const whileOpen = (rounds: Round[]) =>
  rounds[0]?.state === "open" ? POLL_MS : undefined;

function Actions({
  open,
  onChanged,
}: Readonly<{ open: Round | undefined; onChanged: () => void }>) {
  const { data: settings } = useApi<SettingsView>("/api/settings");
  const [asking, setAsking] = useState<"start" | "stop" | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const go = () => {
    setBusy(true);
    const request =
      asking === "stop" && open
        ? postJson(`/api/rounds/${String(open.id)}/stop`)
        : postJson("/api/rounds");
    request
      .then(() => {
        setFailure(null);
        onChanged();
      })
      .catch((error: unknown) => {
        setFailure(
          `Could not ${asking === "stop" ? "stop" : "start"} the round: ${reasonOf(error)}`,
        );
      })
      .finally(() => {
        setAsking(null);
        setBusy(false);
      });
  };

  return (
    <>
      {open ? (
        <button
          type="button"
          className="secondary"
          onClick={() => {
            setAsking("stop");
          }}
        >
          Stop the round
        </button>
      ) : (
        <button
          type="button"
          className="primary"
          onClick={() => {
            setAsking("start");
          }}
        >
          Start a round
        </button>
      )}
      {failure && <p role="alert">{failure}</p>}
      {asking === "start" && (
        <ConfirmDialog
          title="Start a round?"
          confirmLabel="Start the round"
          busy={busy}
          onConfirm={go}
          onCancel={() => {
            setAsking(null);
          }}
        >
          <p>
            The improver can spend up to{" "}
            {settings ? money(settings.values.spending_limit) : "its limit"} on
            its own model calls, the evaluations and the judge. If the current
            version has no scores yet, the round evaluates it first. Only one
            round is open at a time.
          </p>
        </ConfirmDialog>
      )}
      {asking === "stop" && (
        <ConfirmDialog
          title="Stop this round?"
          confirmLabel="Stop the round"
          busy={busy}
          onConfirm={go}
          onCancel={() => {
            setAsking(null);
          }}
        >
          <p>
            The current version stays as it is. Work in hand ends before the
            round stops, and no new claim or model call starts.
          </p>
        </ConfirmDialog>
      )}
    </>
  );
}

function Current({
  round,
  canDecide,
  onChanged,
}: Readonly<{ round: Round; canDecide: boolean; onChanged: () => void }>) {
  return (
    <>
      <section className="panel" aria-label="Current round">
        <h3>
          Round {round.id} <RoundChip state={round.state} />
        </h3>
        <p className="muted">
          Started by {round.started_by}, {dateTime(round.started_at)}. It
          follows recipe version {round.recipe_version} and has spent{" "}
          {money(round.cost)} of its {money(round.settings.spending_limit)}.
        </p>
        <Stages round={round} />
        {round.stopped_by && (
          <p>
            Stopped by {round.stopped_by}. The current version stayed as it was.
          </p>
        )}
        {round.state === "stopped" && round.why && <p>{round.why}</p>}
      </section>
      {round.pending && (
        <ProposalPanel
          round={round}
          proposal={round.pending}
          canDecide={canDecide}
          onDone={onChanged}
        />
      )}
    </>
  );
}

function Recipes() {
  const { data, failed } = useApi<RecipeVersion[]>("/api/recipes");
  const [chosen, setChosen] = useState<number | null>(null);
  if (!data) {
    return (
      <p role={failed ? "alert" : "status"}>
        {failed ? "Could not load the recipes." : "Loading the recipes..."}
      </p>
    );
  }
  const shown = data.find((r) => r.version === chosen) ?? data[0];
  return (
    <>
      <ul className="plain versions">
        {data.map((recipe) => (
          <li key={recipe.version}>
            <button
              type="button"
              className="link mono"
              aria-pressed={recipe.version === shown.version}
              onClick={() => {
                setChosen(recipe.version);
              }}
            >
              Recipe {recipe.version}
            </button>{" "}
            <span className="muted">
              {recipe.proposed_by}
              {recipe.approved_by ? `, approved by ${recipe.approved_by}` : ""}
            </span>
          </li>
        ))}
      </ul>
      <h4>What recipe {shown.version} changed</h4>
      {shown.diff.length === 0 ? (
        <pre className="instructions-text">{shown.recipe}</pre>
      ) : (
        <Diff lines={shown.diff} />
      )}
    </>
  );
}

export function Improvement() {
  const me = useMe();
  const canDecide = me?.can_approve_over_limit === true;
  const {
    data: rounds,
    failed,
    reload,
  } = useApi<Round[]>("/api/rounds", whileOpen);
  const open = rounds?.find((r) => r.state === "open");

  let body;
  if (!rounds) {
    body = (
      <p className="panel" role={failed ? "alert" : "status"}>
        {failed
          ? "Could not load the rounds. Reload the page to try again."
          : "Loading the rounds..."}
      </p>
    );
  } else {
    body = (
      <>
        {!canDecide && me && (
          <p className="inset" role="note">
            {me.name} can see the rounds but not start, stop or decide on one.{" "}
            {CANNOT_APPROVE}
          </p>
        )}
        {rounds.length === 0 ? (
          <p className="panel muted">
            No rounds yet. Start a round to have the improver look at the claim
            agent&apos;s mistakes and propose a change.
          </p>
        ) : (
          <Current round={rounds[0]} canDecide={canDecide} onChanged={reload} />
        )}
        {rounds.length > 0 && (
          <section className="panel" aria-label="Rounds so far">
            <h3>Rounds so far</h3>
            <RoundsTable rounds={rounds} />
          </section>
        )}
        <section className="panel" aria-label="Scores of each version tried">
          <h3>Scores of each version tried</h3>
          <ScoreChart rounds={rounds} />
        </section>
        <section className="panel" aria-label="Recipe versions">
          <h3>The improver&apos;s recipe</h3>
          <Recipes />
        </section>
      </>
    );
  }

  return (
    <main className="page stack">
      <PageHeader
        title="Improvement"
        purpose="The improver proposes changes to the claim agent. You approve each one, and the backend keeps or rolls it back on the held-out claims."
      >
        {canDecide && rounds && <Actions open={open} onChanged={reload} />}
      </PageHeader>
      {body}
    </main>
  );
}
