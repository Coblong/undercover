export type State =
  | "waiting"
  | "in_progress"
  | "waiting_for_sign_off"
  | "decided"
  | "left_for_handler";

export type Verdict = "approve" | "decline" | "refer";

export interface ClaimSummary {
  claim_id: string;
  customer_name: string;
  claim_type: string;
  summary: string;
  amount_claimed: number;
  submitted_on: string;
  state: State;
}

export interface Item {
  description: string;
  category: string;
  amount: number;
}

export interface ClaimDocument {
  name: string;
  type: string;
  content: string;
}

export interface Decision {
  decision: Verdict;
  payout: number;
  explanation: string;
  policy_section: string;
  decided_at: string;
}

export interface Payment {
  amount: number;
  paid_at: string;
}

export interface SignOff {
  handler_id: string;
  handler_name: string;
  signed_off_at: string;
}

export interface ClaimDetail extends ClaimSummary {
  policy_id: string;
  customer_id: string;
  incident_date: string;
  incident_location: string;
  story: string;
  items: Item[];
  documents: ClaimDocument[];
  decision: Decision | null;
  payment: Payment | null;
  sign_off: SignOff | null;
}

export interface Policy {
  policy_id: string;
  plan: "economy" | "premium" | "first";
  add_ons: string[];
  purchased_on: string;
  trip: {
    destination: string;
    departs_on: string;
    returns_on: string;
    trip_cost: number;
  };
  travelers: string[];
}

export interface PreviousClaim {
  claim_id: string;
  filed_on: string;
  claim_type: string;
  summary: string;
  outcome: string;
  amount_paid: number;
}

export interface ClaimHistory {
  customer_id: string;
  previous_claims: PreviousClaim[];
}

export interface Step {
  id: number;
  kind: "tool_call" | "tool_result" | "message" | "read" | "error";
  text: string;
  created_at: string;
}

export interface Handler {
  handler_id: string;
  name: string;
  role: string;
}

export type TodoStatus = "to_do" | "in_progress" | "done" | "left_for_handler";

export interface Todo {
  text: string;
  status: TodoStatus;
}

export interface Run {
  run_id: number;
  limit: number;
  state: "running" | "finished" | "failed";
  summary: string;
  started_at: string;
  ended_at: string | null;
  todos: Todo[];
}
