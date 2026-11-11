import { Chip } from "./Chip";
import { dateTime, gradeOf, modelCost, percent } from "./format";
import type { Round, Scores } from "./types";
import type { Tone } from "./format";

const STATE_TONE: Record<Round["state"], Tone> = {
  open: "cerulean",
  finished: "seagreen",
  stopped: "coral",
};

const STATE_LABEL: Record<Round["state"], string> = {
  open: "Open",
  finished: "Finished",
  stopped: "Stopped",
};

export function RoundChip({ state }: Readonly<{ state: Round["state"] }>) {
  return <Chip tone={STATE_TONE[state]} label={STATE_LABEL[state]} />;
}

function Verdict({ round }: Readonly<{ round: Round }>) {
  if (round.verdict === null) {
    return <span className="muted">No version tried</span>;
  }
  return (
    <Chip
      tone={round.verdict === "kept" ? "seagreen" : "raspberry"}
      label={round.verdict === "kept" ? "Kept" : "Rolled back"}
    />
  );
}

function Pair({
  before,
  after,
  show,
}: Readonly<{
  before: Scores | null;
  after: Scores | null;
  show: (scores: Scores) => string;
}>) {
  if (!before || !after) return <span className="muted">-</span>;
  return (
    <span className="mono">
      {show(before)} to {show(after)}
    </span>
  );
}

function Decisions({ round }: Readonly<{ round: Round }>) {
  if (round.decisions.length === 0) return <span className="muted">-</span>;
  return (
    <ul className="plain">
      {round.decisions.map((d) => (
        <li key={`${d.kind}-${d.decided_at}-${d.outcome}`}>
          {d.outcome === "approved" ? "Approved" : "Rejected"} the {d.kind} -{" "}
          {d.handler}, {dateTime(d.decided_at)}
          {d.reason ? ` ("${d.reason}")` : ""}
        </li>
      ))}
    </ul>
  );
}

/** Every round, newest first, with what it tried, how it scored and who decided. */
export function RoundsTable({ rounds }: Readonly<{ rounds: Round[] }>) {
  return (
    <div className="table-scroll">
      <table aria-label="Rounds">
        <thead>
          <tr>
            <th scope="col">Round</th>
            <th scope="col">State</th>
            <th scope="col">Started</th>
            <th scope="col" className="num">
              Recipe
            </th>
            <th scope="col" className="num">
              Version tried
            </th>
            <th scope="col">Verdict</th>
            <th scope="col">Why</th>
            <th scope="col" className="num">
              Held-out accuracy
            </th>
            <th scope="col" className="num">
              Held-out grade
            </th>
            <th scope="col">Decisions</th>
            <th scope="col" className="num">
              Cost
            </th>
          </tr>
        </thead>
        <tbody>
          {rounds.map((round) => (
            <tr key={round.id}>
              <th scope="row" className="mono">
                {round.id}
              </th>
              <td>
                <RoundChip state={round.state} />
              </td>
              <td>
                {round.started_by}, {dateTime(round.started_at)}
              </td>
              <td className="num mono">{round.recipe_version}</td>
              <td className="num mono">{round.tried_version ?? "-"}</td>
              <td>
                <Verdict round={round} />
              </td>
              <td>
                {round.why ?? ""}
                {round.reason ? (
                  <span className="muted block">{round.reason}</span>
                ) : null}
              </td>
              <td className="num">
                <Pair
                  before={round.held_out_before}
                  after={round.held_out_after}
                  show={(s) => percent(s.accuracy)}
                />
              </td>
              <td className="num">
                <Pair
                  before={round.held_out_before}
                  after={round.held_out_after}
                  show={(s) => gradeOf(s.grade)}
                />
              </td>
              <td>
                <Decisions round={round} />
              </td>
              <td className="num mono">{modelCost(round.cost)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
