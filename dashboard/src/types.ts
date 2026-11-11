// The API's types are generated from the backend (npm run types). This file
// names the ones the dashboard uses.
import type { ClaimSummary, Decision, Dispatch } from "./api-types";

export type {
  ClaimDetail,
  ClaimHistory,
  ClaimSummary,
  ConfigurationVersion,
  Decision,
  Dispatch,
  DispatchClaim,
  DispatchSummary,
  DiffLine,
  Document as ClaimDocument,
  Evaluation,
  EvaluationClaim,
  EvaluationDetail,
  Handler,
  Item,
  Payment,
  Policy,
  PreviousClaim,
  SettingsValues,
  SettingsView,
  SignOff,
  Step,
} from "./api-types";

export type State = ClaimSummary["state"];
export type Verdict = Decision["decision"];
export type RunState = Dispatch["state"];
