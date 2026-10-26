// The API's types are generated from the backend (npm run types). This file
// names the ones the dashboard uses.
import type { ClaimSummary, Decision, Todo } from "./api-types";

export type {
  ClaimDetail,
  ClaimHistory,
  ClaimSummary,
  Decision,
  Document as ClaimDocument,
  Handler,
  Item,
  Payment,
  Policy,
  PreviousClaim,
  Run,
  SignOff,
  Step,
  Todo,
} from "./api-types";

export type State = ClaimSummary["state"];
export type Verdict = Decision["decision"];
export type TodoStatus = Todo["status"];
