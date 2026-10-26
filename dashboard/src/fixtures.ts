import { vi } from "vitest";
import type {
  ClaimDetail,
  ClaimHistory,
  ClaimSummary,
  Policy,
  Run,
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
  payment: { amount: 420, paid_at: "2026-10-15T10:01:00+00:00" },
  sign_off: null,
};

export const policy: Policy = {
  policy_id: "POL-2001",
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

export const runningRun: Run = {
  run_id: 1,
  limit: 5,
  state: "running",
  summary: "",
  started_at: "2026-10-26T10:00:00+00:00",
  ended_at: null,
  todos: [
    { text: "CLM-1003 passport", status: "done" },
    { text: "CLM-1001 delay", status: "in_progress" },
    { text: "CLM-1002 baggage", status: "to_do" },
    { text: "CLM-1004 medical", status: "left_for_handler" },
  ],
};

export const finishedRun: Run = {
  ...runningRun,
  state: "finished",
  summary: "Three claims decided. One is left for a handler.",
  ended_at: "2026-10-26T10:10:00+00:00",
};
