import { vi } from "vitest";
import type {
  ClaimDetail,
  ClaimHistory,
  ClaimSummary,
  ConfigurationVersion,
  Dispatch,
  Evaluation,
  EvaluationDetail,
  Handler,
  Policy,
  Round,
  SettingsView,
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
    approval_limit: 1000,
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

export const dana: Handler = {
  handler_id: "HND-01",
  name: "Dana Whitfield",
  role: "Senior Claims Handler",
  can_approve_over_limit: true,
};

export const marcus: Handler = {
  handler_id: "HND-02",
  name: "Marcus Oyelaran",
  role: "Claims Handler",
  can_approve_over_limit: false,
};

export const waitingForSignOff: ClaimDetail = {
  ...claim,
  state: "waiting_for_sign_off",
  decision: {
    decision: "approve",
    payout: 1250,
    explanation: "Covered.",
    policy_section: "5. Baggage and Personal Effects",
    decided_at: "2026-10-15T10:00:00+00:00",
    approval_limit: 1000,
  },
};

export const runningDispatch: Dispatch = {
  run_id: 4,
  concurrency: 3,
  model: "openai/gpt-6-luna",
  reasoning_effort: "low",
  config_version: 1,
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

export const finishedEvaluationRow: Evaluation = {
  run_id: 3,
  eval_set: "held_out",
  tiny: true,
  concurrency: 5,
  model: "openai/gpt-6-luna",
  reasoning_effort: "low",
  config_version: 1,
  state: "finished",
  started_at: "2026-11-03T10:00:00+00:00",
  ended_at: "2026-11-03T10:01:30+00:00",
  claim_count: 2,
  measures: {
    decision_accuracy: 0.5,
    accuracy: 0.5,
    cost_of_mistakes: 1250,
    straight_through: 1,
    cost_per_claim: 0.0123,
    seconds_per_claim: 31.5,
    run_seconds: 90,
    grade: 1.75,
    judge_cost: 0.0004,
  },
};

export const finishedEvaluation: EvaluationDetail = {
  ...finishedEvaluationRow,
  results: [
    {
      claim_id: "EV3-CLM-1104",
      source_id: "CLM-1104",
      state: "decided",
      handler_decision: "approve",
      handler_payout: 250,
      agent_decision: "approve",
      agent_payout: 250,
      reason: "Covered.",
      decision_matches: true,
      right: true,
      mistake_cost: 0,
      cost: 0.01,
      seconds: 30,
      grade: 2.5,
      letter: "B",
      same_grounds: true,
      same_section: false,
    },
    {
      claim_id: "EV3-CLM-1114",
      source_id: "CLM-1114",
      state: "decided",
      handler_decision: "decline",
      handler_payout: 0,
      agent_decision: "approve",
      agent_payout: 1250,
      reason: "Covered.",
      decision_matches: false,
      right: false,
      mistake_cost: 1250,
      cost: 0.0146,
      seconds: 33,
      grade: null,
      letter: null,
      same_grounds: null,
      same_section: null,
    },
  ],
};

export const evaluationCopy: ClaimDetail = {
  ...waitingForSignOff,
  claim_id: "EV3-CLM-1114",
  state: "decided",
  needs_sign_off: true,
  evaluation_id: 3,
};

export const versionOne: ConfigurationVersion = {
  version: 1,
  instructions: "Decide the claim.\nUse the wording.",
  model: "openai/gpt-6-luna",
  reasoning_effort: "low",
  reason: "The claim agent as it was built.",
  proposed_by: "Seed",
  approved_by: null,
  approved_at: null,
  created_at: "2026-11-01T09:00:00+00:00",
  current: false,
  previous_model: null,
  previous_reasoning_effort: null,
  diff: [],
  held_out: {
    runs: 2,
    accuracy: 0.64,
    grade: 1.4,
    cost_per_claim: 0.001,
    seconds_per_claim: 20,
  },
  evaluation: null,
};

export const versionTwo: ConfigurationVersion = {
  ...versionOne,
  version: 2,
  instructions: "Decide the claim.\nUse the wording.\nNever guess.",
  model: "google/gemini-3.5-flash-lite",
  reasoning_effort: "minimal",
  reason: "Approvals of declined claims need a rule.",
  proposed_by: "Improver, round 1",
  approved_by: "Dana Whitfield",
  approved_at: "2026-11-02T10:00:00+00:00",
  current: true,
  held_out: null,
  previous_model: "openai/gpt-6-luna",
  previous_reasoning_effort: "low",
  diff: [
    { kind: "context", text: "Use the wording." },
    { kind: "remove", text: "Old line" },
    { kind: "add", text: "Never guess." },
  ],
};

export const settingsView: SettingsView = {
  values: {
    approval_limit: 1000,
    objectives: { accuracy: 0.6, grade: 1 },
    noise: {
      accuracy: 0.1,
      grade: 0.15,
      cost_per_claim: 0.15,
      seconds_per_claim: 0.15,
    },
    tolerance: {
      accuracy: 0.1,
      grade: 0.15,
      cost_per_claim: 1,
      seconds_per_claim: 0.25,
    },
    spending_limit: 0.5,
    agents_at_once: 25,
  },
  changes: [
    {
      key: "agents_at_once",
      changed_by: "Dana Whitfield",
      changed_at: "2026-11-03T10:00:00+00:00",
    },
    {
      key: "approval_limit",
      changed_by: null,
      changed_at: "2026-11-01T09:00:00+00:00",
    },
  ],
  models: [
    {
      model: "openai/gpt-6-luna",
      reasoning_efforts: ["none", "low", "medium"],
      input_price: 0.1,
      output_price: 0.5,
    },
  ],
};

export const roundSettings = settingsView.values;

export const waitingRound: Round = {
  id: 2,
  state: "open",
  stage: "change_waiting",
  done: 0,
  of: 0,
  settings: roundSettings,
  recipe_version: 1,
  started_by: "Dana Whitfield",
  started_at: "2026-11-04T10:00:00+00:00",
  ended_at: null,
  stopped_by: null,
  tried_version: null,
  reason: null,
  held_out_before: null,
  held_out_after: null,
  verdict: null,
  why: null,
  pending: {
    kind: "change",
    version: 2,
    reason: "Approvals of declined claims need a rule.",
    model: "google/gemini-3.5-flash-lite",
    reasoning_effort: "minimal",
    previous_model: "openai/gpt-6-luna",
    previous_reasoning_effort: "low",
    diff: [
      { kind: "context", text: "Use the wording." },
      { kind: "remove", text: "Old line" },
      { kind: "add", text: "Never guess." },
    ],
  },
  decisions: [],
  cost: 0.1234,
  improver_cost: 0.02,
  spending_left: 0.3766,
  needs: { evaluation: 0, held_out: 0 },
  thread_id: "round-2-abcd",
};

export const finishedRound: Round = {
  ...waitingRound,
  id: 1,
  state: "finished",
  stage: "finished",
  tried_version: 2,
  reason: "Approvals of declined claims need a rule.",
  held_out_before: {
    runs: 2,
    accuracy: 0.64,
    grade: 1.4,
    cost_per_claim: 0.001,
    seconds_per_claim: 20,
  },
  held_out_after: {
    runs: 2,
    accuracy: 0.8,
    grade: 1.7,
    cost_per_claim: 0.0011,
    seconds_per_claim: 21,
  },
  verdict: "kept",
  why: "accuracy improved",
  pending: null,
  decisions: [
    {
      kind: "change",
      outcome: "approved",
      handler: "Dana Whitfield",
      reason: null,
      version: 2,
      decided_at: "2026-11-03T10:30:00+00:00",
    },
    {
      kind: "recipe",
      outcome: "rejected",
      handler: "Dana Whitfield",
      reason: "Too long.",
      version: null,
      decided_at: "2026-11-03T11:00:00+00:00",
    },
  ],
  cost: 0.2,
  improver_cost: 0.05,
  spending_left: 0.3,
};

export const recipes = [
  {
    version: 2,
    recipe: "1. Read.\n2. Propose.",
    reason: "Shorter.",
    proposed_by: "Improver, round 1",
    approved_by: "Dana Whitfield",
    approved_at: "2026-11-03T11:30:00+00:00",
    created_at: "2026-11-03T11:30:00+00:00",
    current: true,
    diff: [
      { kind: "remove", text: "1. Call read_results." },
      { kind: "add", text: "1. Read." },
    ],
  },
  {
    version: 1,
    recipe: "1. Call read_results.",
    reason: "The first recipe.",
    proposed_by: "Seed",
    approved_by: null,
    approved_at: null,
    created_at: "2026-11-01T09:00:00+00:00",
    current: false,
    diff: [],
  },
];
