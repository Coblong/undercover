import { useState } from "react";
import { putJson, reasonOf, useApi } from "./api";
import { ConfirmDialog } from "./ConfirmDialog";
import { date, money } from "./format";
import { CANNOT_APPROVE, useMe } from "./Me";
import { PageHeader } from "./PageHeader";
import {
  APPEARANCES,
  getAppearance,
  setAppearance,
  type Appearance,
} from "./theme";
import type { SettingsValues, SettingsView } from "./types";

type Thresholds = SettingsValues["noise"];

interface Field {
  id: string;
  group: string;
  label: string;
  unit: string;
  percent: boolean;
  read: (values: SettingsValues) => number;
  write: (values: SettingsValues, value: number) => SettingsValues;
}

function threshold(
  group: "noise" | "tolerance",
  name: keyof Thresholds,
  label: string,
  unit: string,
  percent: boolean,
): Field {
  return {
    id: `${group}.${name}`,
    group,
    label,
    unit,
    percent,
    read: (values) => values[group][name],
    write: (values, value) => ({
      ...values,
      [group]: { ...values[group], [name]: value },
    }),
  };
}

const FIELDS: Field[] = [
  {
    id: "approval_limit",
    group: "approval_limit",
    label: "Approval limit",
    unit: "US dollars",
    percent: false,
    read: (v) => v.approval_limit,
    write: (v, value) => ({ ...v, approval_limit: value }),
  },
  {
    id: "objectives.accuracy",
    group: "objectives",
    label: "Floor on held-out accuracy",
    unit: "percent",
    percent: true,
    read: (v) => v.objectives.accuracy,
    write: (v, value) => ({
      ...v,
      objectives: { ...v.objectives, accuracy: value },
    }),
  },
  {
    id: "objectives.grade",
    group: "objectives",
    label: "Floor on held-out grade",
    unit: "0 to 3",
    percent: false,
    read: (v) => v.objectives.grade,
    write: (v, value) => ({
      ...v,
      objectives: { ...v.objectives, grade: value },
    }),
  },
  threshold("noise", "accuracy", "Noise in accuracy", "percent", true),
  threshold("noise", "grade", "Noise in the grade", "grade points", false),
  threshold(
    "noise",
    "cost_per_claim",
    "Noise in cost per claim",
    "percent",
    true,
  ),
  threshold(
    "noise",
    "seconds_per_claim",
    "Noise in time per claim",
    "percent",
    true,
  ),
  threshold("tolerance", "accuracy", "Tolerance in accuracy", "percent", true),
  threshold(
    "tolerance",
    "grade",
    "Tolerance in the grade",
    "grade points",
    false,
  ),
  threshold(
    "tolerance",
    "cost_per_claim",
    "Tolerance in cost per claim",
    "percent",
    true,
  ),
  threshold(
    "tolerance",
    "seconds_per_claim",
    "Tolerance in time per claim",
    "percent",
    true,
  ),
  {
    id: "spending_limit",
    group: "spending_limit",
    label: "Spending limit for each round",
    unit: "US dollars",
    percent: false,
    read: (v) => v.spending_limit,
    write: (v, value) => ({ ...v, spending_limit: value }),
  },
  {
    id: "agents_at_once",
    group: "agents_at_once",
    label: "Claim agents at once in a round",
    unit: "1 to 25",
    percent: false,
    read: (v) => v.agents_at_once,
    write: (v, value) => ({ ...v, agents_at_once: value }),
  },
];

function shown(field: Field, values: SettingsValues): string {
  const value = field.read(values);
  return String(field.percent ? Number((value * 100).toFixed(4)) : value);
}

function Appearance() {
  const [chosen, setChosen] = useState<Appearance>(getAppearance());
  return (
    <section className="panel" aria-label="Appearance">
      <h3>Appearance</h3>
      <p className="muted">This browser remembers your choice.</p>
      <div className="choices" role="radiogroup" aria-label="Appearance">
        {(Object.keys(APPEARANCES) as Appearance[]).map((name) => (
          <label key={name} className="inline">
            <input
              type="radio"
              name="appearance"
              checked={chosen === name}
              onChange={() => {
                setAppearance(name);
                setChosen(name);
              }}
            />
            {APPEARANCES[name]}
          </label>
        ))}
      </div>
    </section>
  );
}

function SettingsForm({
  view,
  onSaved,
}: Readonly<{ view: SettingsView; onSaved: (view: SettingsView) => void }>) {
  const me = useMe();
  const canChange = me?.can_approve_over_limit === true;
  const [text, setText] = useState<Record<string, string>>(() =>
    Object.fromEntries(FIELDS.map((f) => [f.id, shown(f, view.values)])),
  );
  const [proposed, setProposed] = useState<SettingsValues | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const changes = new Map(view.changes.map((c) => [c.key, c]));

  const review = () => {
    let next = view.values;
    for (const field of FIELDS) {
      const typed = Number(text[field.id]);
      if (text[field.id].trim() === "" || !Number.isFinite(typed)) {
        setFailure(`${field.label} needs a number.`);
        return;
      }
      next = field.write(next, field.percent ? typed / 100 : typed);
    }
    setFailure(null);
    setProposed(next);
  };

  const save = () => {
    if (!proposed) return;
    setBusy(true);
    putJson<SettingsView>("/api/settings", proposed)
      .then((saved) => {
        setProposed(null);
        onSaved(saved);
      })
      .catch((error: unknown) => {
        setProposed(null);
        setFailure(`Could not save the settings: ${reasonOf(error)}`);
      })
      .finally(() => {
        setBusy(false);
      });
  };

  return (
    <form
      className="panel"
      aria-label="Settings"
      onSubmit={(event) => {
        event.preventDefault();
        review();
      }}
    >
      {!canChange && me && (
        <p className="inset" role="note">
          {me.name} can see the settings but not change them. {CANNOT_APPROVE}
        </p>
      )}
      <table>
        <thead>
          <tr>
            <th scope="col">Setting</th>
            <th scope="col" className="num">
              Value
            </th>
            <th scope="col">Unit</th>
            <th scope="col">Last changed</th>
          </tr>
        </thead>
        <tbody>
          {FIELDS.map((field) => {
            const change = changes.get(field.group);
            return (
              <tr key={field.id}>
                <th scope="row">
                  <label htmlFor={field.id}>{field.label}</label>
                </th>
                <td className="num">
                  <input
                    id={field.id}
                    type="number"
                    step="any"
                    className="mono"
                    disabled={!canChange}
                    value={text[field.id]}
                    onChange={(event) => {
                      setText({ ...text, [field.id]: event.target.value });
                    }}
                  />
                </td>
                <td className="muted">{field.unit}</td>
                <td className="muted">
                  {change?.changed_by
                    ? `${change.changed_by}, ${date(change.changed_at)}`
                    : "Starting value"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {canChange && (
        <div className="actions">
          <button type="submit" className="primary">
            Save settings
          </button>
        </div>
      )}
      {failure && <p role="alert">{failure}</p>}
      {proposed && (
        <ConfirmDialog
          title="Save these settings?"
          confirmLabel="Save settings"
          busy={busy}
          onConfirm={save}
          onCancel={() => {
            setProposed(null);
          }}
        >
          <p>
            The approval limit will be {money(proposed.approval_limit)}. A new
            limit applies to the decisions recorded after it. A round works to
            the settings as they stood when it started.
          </p>
        </ConfirmDialog>
      )}
    </form>
  );
}

function Models({ view }: Readonly<{ view: SettingsView }>) {
  return (
    <section className="panel table-scroll" aria-label="Models allowed">
      <h3>Models the claim agent may use</h3>
      <p className="muted">
        Set in a file, so they can&apos;t be changed here. Prices are US dollars
        per million tokens.
      </p>
      <table>
        <thead>
          <tr>
            <th scope="col">Model</th>
            <th scope="col">Reasoning efforts</th>
            <th scope="col" className="num">
              Input price
            </th>
            <th scope="col" className="num">
              Output price
            </th>
          </tr>
        </thead>
        <tbody>
          {view.models.map((model) => (
            <tr key={model.model}>
              <td className="mono">{model.model}</td>
              <td>{model.reasoning_efforts.join(", ")}</td>
              <td className="num mono">{money(model.input_price)}</td>
              <td className="num mono">{money(model.output_price)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function Settings() {
  const { data, failed } = useApi<SettingsView>("/api/settings");
  const [saved, setSaved] = useState<{
    view: SettingsView;
    count: number;
  } | null>(null);
  const view = saved?.view ?? data;

  return (
    <main className="page stack">
      <PageHeader
        title="Settings"
        purpose="The approval limit, the business objectives and the limits on the improver."
      />
      {view ? (
        <>
          <SettingsForm
            key={saved?.count ?? 0}
            view={view}
            onSaved={(next) => {
              setSaved({ view: next, count: (saved?.count ?? 0) + 1 });
            }}
          />
          <Models view={view} />
        </>
      ) : (
        <p className="panel" role={failed ? "alert" : "status"}>
          {failed
            ? "Could not load the settings. Reload the page to try again."
            : "Loading the settings..."}
        </p>
      )}
      <Appearance />
    </main>
  );
}
