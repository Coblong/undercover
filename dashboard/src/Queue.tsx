import { Chip } from "./Chip";
import { money, STATE_LABEL, STATE_TONE, STATES, words } from "./format";
import type { ClaimSummary } from "./types";

interface Props {
  claims: ClaimSummary[];
  selected: string | null;
  onSelect: (claimId: string) => void;
}

export function Queue({ claims, selected, onSelect }: Readonly<Props>) {
  return (
    <section className="panel queue" aria-label="Queue">
      <h2>Queue</h2>
      <ul className="counts">
        {STATES.map((state) => (
          <li key={state} className={`count tone-${STATE_TONE[state]}`}>
            <span className="number">
              {claims.filter((claim) => claim.state === state).length}
            </span>
            <span>{STATE_LABEL[state]}</span>
          </li>
        ))}
      </ul>
      <ul className="claims">
        {claims.map((claim) => (
          <li key={claim.claim_id}>
            <button
              type="button"
              aria-pressed={claim.claim_id === selected}
              onClick={() => {
                onSelect(claim.claim_id);
              }}
            >
              <span className="row-top">
                <span className="mono">{claim.claim_id}</span>
                <Chip
                  tone={STATE_TONE[claim.state]}
                  label={STATE_LABEL[claim.state]}
                />
              </span>
              <span>{claim.summary}</span>
              <span className="row-bottom muted">
                <span>
                  {claim.customer_name}, {words(claim.claim_type).toLowerCase()}
                </span>
                <span className="mono">{money(claim.amount_claimed)}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
