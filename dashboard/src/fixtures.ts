import { vi } from "vitest";
import type {
  ClaimDetail,
  ClaimHistory,
  ClaimSummary,
  Dispatch,
  Policy,
} from "./types";

export const summaries: ClaimSummary[] = [
  {
    claim_id: "CLM-1001",
    customer_name: "Maya Patel",
    claim_type: "trip_delay",
    summary: "Flight home canceled by the airline",
    amount_claimed: 420,
    submitted_on: "2026-06-24",
    state: "waiting",
  },
  {
    claim_id: "CLM-1002",
    customer_name: "Tom Becker",
    claim_type: "baggage",
    summary: "Suitcase lost on a connecting flight",
    amount_claimed: 1950,
    submitted_on: "2026-07-20",
    state: "waiting_for_sign_off",
  },
  {
    claim_id: "CLM-1003",
    customer_name: "Aiko Tanaka",
    claim_type: "passport",
    summary: "Passport stolen in Lisbon",
    amount_claimed: 210,
    submitted_on: "2026-07-02",
    state: "decided",
  },
];

export const claim: ClaimDetail = {
  ...summaries[0],
  policy_id: "POL-2001",
  customer_id: "CUS-3001",
  incident_date: "2026-06-18",
  incident_location: "Rome, Italy",
  story: "My flight home from Rome was canceled.",
  items: [
    { description: "Airport hotel", category: "accommodation", amount: 245 },
    { description: "Taxis", category: "transport", amount: 175 },
  ],
  documents: [
    {
      name: "Hotel receipt",
      type: "receipt",
      content: "Hotel Aurelia, $245.00",
    },
  ],
  decision: null,
  payment: null,
  sign_off: null,
  needs_sign_off: false,
  evaluation_id: null,
};

export const decidedClaim: ClaimDetail = {
  ...claim,
  state: "decided",
  decision: {
    decision: "approve",
    payout: 420,
    explanation:
      "The delay passed the waiting period and the costs are covered.",
    policy_section: "3. Trip Delay",
    decided_at: "2026-10-15T10:00:00+00:00",
  },
  payment: {
    amount: 420,
    paid_at: "2026-10-15T10:01:00+00:00",
    signed_off_by: null,
    signed_off_at: null,
  },
  sign_off: null,
};

export const policy: Policy = {
  policy_id: "POL-2001",
  customer_id: "CUS-3001",
  sold_through: "direct",
  status: "active",
  plan: "premium",
  add_ons: [],
  purchased_on: "2026-04-22",
  trip: {
    destination: "Rome, Italy",
    departs_on: "2026-06-10",
    returns_on: "2026-06-18",
    trip_cost: 3100,
  },
  travelers: ["Maya Patel"],
};

export const history: ClaimHistory = {
  customer_id: "CUS-3001",
  previous_claims: [
    {
      claim_id: "CLM-0412",
      filed_on: "2024-08-19",
      incident_date: "2024-08-09",
      claim_type: "baggage_delay",
      summary: "Checked bag arrived 30 hours late in Lisbon",
      outcome: "approved",
      amount_paid: 140,
    },
  ],
};

export const claimRoutes = {
  "/api/policies/POL-2001": policy,
  "/api/claims/CLM-1001/claim-history": history,
};

/** An answer the backend refuses with, as a status and a body. */
export class Refusal {
  constructor(
    readonly status: number,
    readonly body: unknown,
  ) {}
}

/**
 * Answer fetch calls from a table of "METHOD path" or "path" to JSON bodies.
 * A function in the table is called each time, so an answer can change.
 */
export function stubApi(routes: Record<string, unknown>) {
  const fetchStub = vi.fn((path: string, init?: RequestInit) => {
    const key = init?.method ? `${init.method} ${path}` : path;
    if (!(key in routes)) {
      return Promise.resolve(new Response("{}", { status: 404 }));
    }
    const answer = routes[key];
    if (answer instanceof Refusal) {
      return Promise.resolve(
        new Response(JSON.stringify(answer.body), { status: answer.status }),
      );
    }
    const body =
      typeof answer === "function" ? (answer as () => unknown)() : answer;
    return Promise.resolve(new Response(JSON.stringify(body)));
  });
  vi.stubGlobal("fetch", fetchStub);
  return fetchStub;
}

export const handlers = [
  {
    handler_id: "HND-01",
    name: "Dana Whitfield",
    role: "Senior Claims Handler",
  },
  { handler_id: "HND-02", name: "Marcus Oyelaran", role: "Claims Handler" },
];

export const waitingForSignOff: ClaimDetail = {
  ...claim,
  state: "waiting_for_sign_off",
  decision: {
    decision: "approve",
    payout: 1250,
    explanation: "Covered.",
    policy_section: "5. Baggage and Personal Effects",
    decided_at: "2026-10-15T10:00:00+00:00",
  },
};

export const runningDispatch: Dispatch = {
  run_id: 4,
  concurrency: 3,
  model: "openai/gpt-6-luna",
  reasoning_effort: "low",
  state: "running",
  started_at: "2026-11-03T10:00:00+00:00",
  ended_at: null,
  seconds: 75.4,
  claim_seconds: 130,
  cost: 0.0431,
  summary: null,
  claims: [
    {
      claim_id: "CLM-1001",
      state: "decided",
      decision: "approve",
      payout: 420,
      started_at: "2026-11-03T10:00:01+00:00",
      seconds: 42.5,
      cost: 0.0123,
    },
    {
      claim_id: "CLM-1002",
      state: "in_progress",
      decision: null,
      payout: null,
      started_at: "2026-11-03T10:00:02+00:00",
      seconds: 12,
      cost: 0.0044,
    },
    {
      claim_id: "CLM-1003",
      state: "waiting",
      decision: null,
      payout: null,
      started_at: null,
      seconds: 0,
      cost: 0,
    },
  ],
};

export const finishedDispatch: Dispatch = {
  ...runningDispatch,
  state: "finished",
  ended_at: "2026-11-03T10:02:00+00:00",
  seconds: 120,
  summary: {
    approved: 2,
    declined: 1,
    referred: 1,
    waiting_for_sign_off: [{ claim_id: "CLM-1003", payout: 3750 }],
    left_for_handler: ["CLM-1005"],
    in_progress: ["CLM-1006"],
  },
};
