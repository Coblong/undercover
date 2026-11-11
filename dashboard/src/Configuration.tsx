import { useState } from "react";
import { useApi } from "./api";
import { Chip } from "./Chip";
import { Diff } from "./Diff";
import { date, gradeOf, percent } from "./format";
import { PageHeader } from "./PageHeader";
import type { ConfigurationVersion } from "./types";

function Origin({ version }: Readonly<{ version: ConfigurationVersion }>) {
  return (
    <>
      Proposed by {version.proposed_by}
      {version.approved_by
        ? `, approved by ${version.approved_by} on ${date(version.approved_at ?? "")}`
        : ""}
    </>
  );
}

function Change({ version }: Readonly<{ version: ConfigurationVersion }>) {
  const moved =
    version.previous_model !== null &&
    (version.previous_model !== version.model ||
      version.previous_reasoning_effort !== version.reasoning_effort);
  return (
    <section
      className="panel"
      aria-label={`Version ${String(version.version)}`}
    >
      <h3>Version {version.version}</h3>
      <p>{version.reason}</p>
      <p className="muted">
        <Origin version={version} />
      </p>
      {moved && (
        <p>
          Model: {version.previous_model} at {version.previous_reasoning_effort}{" "}
          to {version.model} at {version.reasoning_effort}
        </p>
      )}
      {version.previous_model === null ? (
        <p className="muted">This is the first version.</p>
      ) : (
        <>
          <h4>What changed from version {version.version - 1}</h4>
          <Diff lines={version.diff} />
        </>
      )}
      <details>
        <summary>Instructions in full</summary>
        <pre className="instructions-text">{version.instructions}</pre>
      </details>
    </section>
  );
}

export function Configuration() {
  const { data, failed } = useApi<ConfigurationVersion[]>(
    "/api/configurations",
  );
  const [chosen, setChosen] = useState<number | null>(null);

  let body;
  if (!data) {
    body = (
      <p className="panel" role={failed ? "alert" : "status"}>
        {failed
          ? "Could not load the configuration. Reload the page to try again."
          : "Loading the configuration..."}
      </p>
    );
  } else {
    const current = data.find((v) => v.current) ?? data[0];
    const shown = data.find((v) => v.version === chosen) ?? current;
    body = (
      <>
        <section className="panel" aria-label="Current version">
          <h3>
            Current version{" "}
            <Chip
              tone="seagreen"
              label={`Version ${String(current.version)}`}
            />
          </h3>
          <dl className="facts">
            <dt>Model</dt>
            <dd className="mono">{current.model}</dd>
            <dt>Reasoning effort</dt>
            <dd>{current.reasoning_effort}</dd>
            <dt>Why</dt>
            <dd>{current.reason}</dd>
          </dl>
        </section>
        <section className="panel table-scroll" aria-label="Versions">
          <h3>Versions</h3>
          <table>
            <thead>
              <tr>
                <th scope="col">Version</th>
                <th scope="col">Status</th>
                <th scope="col">Model</th>
                <th scope="col">Effort</th>
                <th scope="col">Proposed by</th>
                <th scope="col">Approved by</th>
                <th scope="col" className="num">
                  Held-out accuracy
                </th>
                <th scope="col" className="num">
                  Held-out grade
                </th>
                <th scope="col" className="num">
                  Runs
                </th>
              </tr>
            </thead>
            <tbody>
              {data.map((version) => (
                <tr
                  key={version.version}
                  aria-selected={version.version === shown.version}
                >
                  <td>
                    <button
                      type="button"
                      className="link mono"
                      onClick={() => {
                        setChosen(version.version);
                      }}
                    >
                      {version.version}
                    </button>
                  </td>
                  <td>
                    {version.current ? (
                      <Chip tone="seagreen" label="Current" />
                    ) : (
                      <Chip tone="marigold" label="Earlier" />
                    )}
                  </td>
                  <td className="mono">{version.model}</td>
                  <td>{version.reasoning_effort}</td>
                  <td>{version.proposed_by}</td>
                  <td>{version.approved_by ?? "-"}</td>
                  <td className="num mono">
                    {version.held_out
                      ? percent(version.held_out.accuracy)
                      : "-"}
                  </td>
                  <td className="num mono">
                    {version.held_out ? gradeOf(version.held_out.grade) : "-"}
                  </td>
                  <td className="num mono">{version.held_out?.runs ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
        <Change version={shown} />
      </>
    );
  }

  return (
    <main className="page stack">
      <PageHeader
        title="Configuration"
        purpose="The claim agent's instructions, model and reasoning effort, kept in versions."
      />
      {body}
    </main>
  );
}
