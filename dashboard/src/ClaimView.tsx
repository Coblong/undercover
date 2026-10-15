import { useApi } from "./api";
import { Chip } from "./Chip";
import {
  date,
  money,
  STATE_LABEL,
  STATE_TONE,
  VERDICT_LABEL,
  VERDICT_TONE,
  words,
} from "./format";
import type {
  ClaimDetail,
  ClaimHistory,
  Decision,
  Item,
  Payment,
  Policy,
  SignOff,
} from "./types";

function DecisionPanel(
  props: Readonly<{
    decision: Decision;
    payment: Payment | null;
    signOff: SignOff | null;
  }>,
) {
  const { decision, payment, signOff } = props;
  return (
    <section className="inset" aria-label="Decision">
      <h3>Decision</h3>
      <p className="verdict">
        <Chip
          tone={VERDICT_TONE[decision.decision]}
          label={VERDICT_LABEL[decision.decision]}
        />
        {decision.decision === "approve" && (
          <span className="mono">{money(decision.payout)}</span>
        )}
      </p>
      <p>{decision.explanation}</p>
      <p className="muted">Policy section: {decision.policy_section}</p>
      {signOff && (
        <p className="muted">
          Signed off by {signOff.handler_name} on {date(signOff.signed_off_at)}.
        </p>
      )}
      {payment && (
        <p>
          Paid <span className="mono">{money(payment.amount)}</span> on{" "}
          {date(payment.paid_at)}.
        </p>
      )}
    </section>
  );
}

function ItemsTable({ items }: Readonly<{ items: Item[] }>) {
  const total = items.reduce((sum, item) => sum + item.amount, 0);
  return (
    <table>
      <thead>
        <tr>
          <th scope="col">Item</th>
          <th scope="col">Category</th>
          <th scope="col" className="num">
            Amount
          </th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.description}>
            <td>{item.description}</td>
            <td>{words(item.category)}</td>
            <td className="num mono">{money(item.amount)}</td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr>
          <th scope="row" colSpan={2}>
            Total claimed
          </th>
          <td className="num mono">{money(total)}</td>
        </tr>
      </tfoot>
    </table>
  );
}

function PolicyPanel({ policyId }: Readonly<{ policyId: string }>) {
  const { data: policy } = useApi<Policy>(`/api/policies/${policyId}`);
  if (!policy) return <p className="muted">Loading policy...</p>;
  return (
    <dl className="facts">
      <dt>Policy</dt>
      <dd className="mono">{policy.policy_id}</dd>
      <dt>Plan</dt>
      <dd>{words(policy.plan)}</dd>
      <dt>Add-ons</dt>
      <dd>
        {policy.add_ons.length ? policy.add_ons.map(words).join(", ") : "None"}
      </dd>
      <dt>Trip</dt>
      <dd>
        {policy.trip.destination}, {date(policy.trip.departs_on)} to{" "}
        {date(policy.trip.returns_on)}
      </dd>
      <dt>Bought</dt>
      <dd>{date(policy.purchased_on)}</dd>
    </dl>
  );
}

function HistoryPanel({ customerId }: Readonly<{ customerId: string }>) {
  const { data: history } = useApi<ClaimHistory>(
    `/api/customers/${customerId}/claim-history`,
  );
  if (!history) return <p className="muted">Loading history...</p>;
  if (!history.previous_claims.length) {
    return <p className="muted">No earlier claims.</p>;
  }
  return (
    <ul className="history">
      {history.previous_claims.map((earlier) => (
        <li key={earlier.claim_id}>
          <span className="mono">{earlier.claim_id}</span>{" "}
          {date(earlier.filed_on)}: {earlier.summary}. {words(earlier.outcome)},{" "}
          <span className="mono">{money(earlier.amount_paid)}</span>
        </li>
      ))}
    </ul>
  );
}

export function ClaimView({ claimId }: Readonly<{ claimId: string }>) {
  const { data: claim, failed } = useApi<ClaimDetail>(`/api/claims/${claimId}`);
  if (!claim) {
    return (
      <p className="panel" role={failed ? "alert" : "status"}>
        {failed ? "Could not load this claim." : "Loading claim..."}
      </p>
    );
  }
  return (
    <article className="panel claim" aria-label={`Claim ${claim.claim_id}`}>
      <header>
        <h2 className="mono">{claim.claim_id}</h2>
        <Chip tone={STATE_TONE[claim.state]} label={STATE_LABEL[claim.state]} />
      </header>
      <p className="lead">{claim.summary}</p>
      <dl className="facts">
        <dt>Customer</dt>
        <dd>{claim.customer_name}</dd>
        <dt>Type</dt>
        <dd>{words(claim.claim_type)}</dd>
        <dt>Incident</dt>
        <dd>
          {date(claim.incident_date)}, {claim.incident_location}
        </dd>
        <dt>Submitted</dt>
        <dd>{date(claim.submitted_on)}</dd>
        <dt>Claimed</dt>
        <dd className="mono">{money(claim.amount_claimed)}</dd>
      </dl>
      {claim.decision && (
        <DecisionPanel
          decision={claim.decision}
          payment={claim.payment}
          signOff={claim.sign_off}
        />
      )}
      <h3>Story</h3>
      <p>{claim.story}</p>
      <h3>Items claimed</h3>
      <ItemsTable items={claim.items} />
      <h3>Documents</h3>
      {claim.documents.map((document) => (
        <details key={document.name}>
          <summary>
            {document.name}{" "}
            <span className="muted">{words(document.type)}</span>
          </summary>
          <p>{document.content}</p>
        </details>
      ))}
      <h3>Policy</h3>
      <PolicyPanel policyId={claim.policy_id} />
      <h3>Earlier claims</h3>
      <HistoryPanel customerId={claim.customer_id} />
    </article>
  );
}
