# Plan: the claims pilot

Builds the pilot from `docs/SPEC.md`: a backend, a dashboard for the handlers and a first UnderCover Agent that decides claims. It follows `docs/TECH_STACK.md`, `docs/CODE_STANDARDS.md`, `docs/PROCESS.md` and `docs/BRAND.md`.

## What exists

A hello-world skeleton with every check in place: `GET /api/health`, an agent with one tool that calls it, a dashboard page that shows whether the backend is up, and one Playwright test. The checks (ruff, pyright, pytest, ESLint, Prettier, tsc, vitest, Playwright) and the pre-commit hook are already wired. No new dependencies are needed: the backend uses the standard library's `sqlite3`, and the packages for FastAPI, LangChain and React are already installed.

## Design decisions

These shape every phase. Please review them first.

1. **Claim states.** `waiting`, `in_progress`, `waiting_for_sign_off` and `decided`, stored on the claim. The records in `data/claims.json` say `open`; the backend loads them as `waiting`.
2. **Where the rules live.** The backend enforces the rules, not the agent's instructions. The agent may try to pay a $1,250 payout; the backend refuses with a 409 until a handler signs off. The limit comes from `data/company.json` (`payout_approval_limit`).
3. **How a decision and a payment work.**
   - `decide_claim` records approve, decline or refer, the payout, the explanation and the policy section. An approval over the limit moves the claim to `waiting_for_sign_off`; anything else moves it to `decided`.
   - `pay_claim` takes the amount. It succeeds only for an approved claim, when the amount equals the approved payout and the payout is within the limit or has been signed off. Otherwise it returns a 409 with a plain message.
   - `sign_off_claim` takes a handler id, records the sign-off and makes the payment. Payments are records only; there are no real payments.
4. **The agent sees only what it needs.**
   - It reaches the backend only through HTTP, with one tool per API operation.
   - The history operation returns the customer's claim history, not their address, phone or date of birth.
   - The backend never loads `data/evals/`, `data/security/`, `claims_manual.md` or `knowledge.json`. A test asserts it.
5. **Instructions.** They live in `backend/instructions.md`. The backend serves them read-only, the agent fetches them at the start of every run to use as its system prompt, and the dashboard shows them.
6. **Watching the agent work.** `run` streams the agent's steps (each tool call and result, and its final message) and posts each one to the backend as a step. The dashboard polls and shows them in order. The backend starts a run in a background thread, so `POST /api/claims/{id}/run` returns at once. If a run crashes with no decision, the backend records a failure step and puts the claim back to `waiting`.
7. **Starting the agent.** From a button on the claim in the dashboard, or from the command line: `uv run -m agent CLM-1001` for one claim, `uv run -m agent --all` for every waiting claim.
8. **Storage.** SQLite in `backend/undercover.db`, git-ignored and seeded from `data/` on first start. Tests and e2e use their own database through the `UNDERCOVER_DB` environment variable.
9. **Tests never call a model.** The agent tests use the existing stub model. The e2e tests do not run the agent; they set up claims through the API (decide, add steps) and check what the dashboard shows. The real model is checked once by hand in Phase 6.

## The API

Every operation has an `operation_id`, a one-line docstring and no optional inputs.

| operation_id | Route | Used by |
|---|---|---|
| `health` | `GET /api/health` | (exists) |
| `list_claims` | `GET /api/claims` | dashboard, agent |
| `get_claim` | `GET /api/claims/{claim_id}` | dashboard, agent |
| `get_policy` | `GET /api/policies/{policy_id}` | dashboard, agent |
| `get_claim_history` | `GET /api/customers/{customer_id}/claim-history` | dashboard, agent |
| `get_policy_wording` | `GET /api/policy-wording` | agent |
| `get_instructions` | `GET /api/instructions` | dashboard, agent |
| `list_handlers` | `GET /api/handlers` | dashboard |
| `list_steps` | `GET /api/claims/{claim_id}/steps` | dashboard |
| `add_step` | `POST /api/claims/{claim_id}/steps` | agent |
| `decide_claim` | `POST /api/claims/{claim_id}/decision` | agent |
| `pay_claim` | `POST /api/claims/{claim_id}/payment` | agent |
| `sign_off_claim` | `POST /api/claims/{claim_id}/sign-off` | dashboard |
| `run_agent` | `POST /api/claims/{claim_id}/run` | dashboard |

## Phase 1: Backend foundations

Builds the database and the read side of the API: the data model, seeding from `data/`, and every `GET` operation. This comes first because everything else reads claims.

- Tables for claims (with state), policies, customers' claim history, handlers, decisions, payments and steps.
- A seed step that loads the claims, policies, customers and handlers, and refuses to touch any other file in `data/`.
- Pydantic response models, and the read operations in the table above.
- `backend/instructions.md` with a placeholder, replaced in Phase 3.

Success criteria:

- [x] `uv run pytest backend` passes, and tests cover seeding (25 claims, 50 policies, 49 customers, 3 handlers), each read operation and the 404 for an unknown id.
- [x] A test asserts the seed never opens anything under `data/evals/` or `data/security/`, nor `claims_manual.md` or `knowledge.json`.
- [x] `GET /api/claims` returns 25 claims, all `waiting`, each with id, type, amount claimed and summary.
- [x] `GET /api/claims/CLM-1001` returns the story, items and documents; `GET /api/policies/POL-2001` returns the Premium plan; `GET /api/customers/CUS-3001/claim-history` returns one earlier claim and no address, phone or date of birth.
- [x] `GET /api/policy-wording` returns the contents of `data/policy_wording.md`.
- [x] `GET /openapi.json` shows every operation with an `operation_id` and a description, and a test fails if one is missing, has no description or has an optional input.
- [x] Starting the backend twice does not duplicate rows.
- [x] `npm run check:python` passes.

## Phase 2: Decisions, payments and the rules

Builds the write side and the state machine, including the rule that a payout over $1,000 is blocked until a handler signs off. This is the part of the spec that must not depend on the agent behaving.

- `decide_claim`, `pay_claim`, `sign_off_claim`, `add_step`, and `list_steps`.
- The rules in design decision 3, with plain-language 409 messages the agent can read and act on.
- A decision is accepted once, only for a claim that is `waiting` or `in_progress`; a payout of 0 for decline and refer; a payout above 0 for approve; amounts are rounded to cents.

Success criteria:

- [x] Tests pass for each path: approve $420 (decided, then payable), approve $1,250 (waiting for sign-off), decline and refer (decided, payment refused), a second decision (409), and an unknown claim (404).
- [x] The key rule has its own tests: paying $1,250 before sign-off returns 409 and creates no payment; after sign-off by `HND-01` the payment exists exactly once; paying a different amount than the approved payout returns 409; paying twice returns 409.
- [x] A test calls `pay_claim` on a claim that was never decided and gets a 409.
- [x] With the backend running, this sequence behaves as described: `decision` approve 1250 on `CLM-1001` gives `waiting_for_sign_off`; `payment` 1250 gives 409; `sign-off` by `HND-01` gives `decided` with a payment of $1,250.00.
- [x] `GET /api/claims` shows the right state for each claim after those calls.
- [x] `npm run check:python` passes with at least 80% coverage.

## Phase 3: The agent

Replaces the hello-world agent with the UnderCover Agent: tools for the API, instructions from the backend, step reporting, the `run_agent` endpoint and a command line.

- One tool per agent-facing operation: `get_claim`, `get_policy`, `get_claim_history`, `get_policy_wording`, `decide_claim`, `pay_claim`. A tool returns the backend's error message on a 409 so the model can recover, and does not raise.
- `run(claim_id, thread_id)` fetches the instructions, builds the agent with `create_agent`, streams it and posts each step with `add_step`. The model choice stays as it is (`openai/gpt-6-luna` with low reasoning, or `MODEL_NAME`).
- The instructions in `backend/instructions.md`: the order of work, to decide only from the policy wording, the deductible and limits, the approve, decline and refer rules, the $1,000 sign-off rule, a payout calculation shown step by step, and an explanation of two or three sentences with the policy section. They say nothing about the claims manual.
- `run_agent` starts a run in the background, sets the claim `in_progress`, and handles a crash as in design decision 6. `python -m agent` runs one claim or `--all`.
- The old `check_backend` tool and its tests are removed.

Success criteria:

- [x] `uv run pytest agent` passes, using the stub model and the backend through `TestClient`. Tests cover: a full run that approves $420 and pays it; a run that declines; a run that approves $1,250 and then tries to pay and receives the 409 text back in the tool result; steps appearing in `list_steps` in order; the instructions coming from the backend.
- [x] A test shows a crashing model leaves the claim `waiting` with a failure step.
- [x] A test shows `POST /api/claims/{id}/run` returns 202 and a second call while running returns 409.
- [x] No tool or code in `agent/` reads files under `data/` or `backend/`; a test asserts the agent module has no filesystem access to either.
- [x] `uv run -m agent --help` prints its usage.
- [x] `npm run check:python` passes.

## Phase 4: Dashboard, the queue and each claim

Builds the first working screens in React and TypeScript, in the brand's look.

- A header with the UnderCover name and two views: Claims and Instructions.
- The queue: counts for the four states, then one row per claim with id, type, summary, amount claimed and a state label. Selecting a row shows the claim beside the queue.
- The claim in full: story, items, documents, policy and plan, the customer's earlier claims, and the agent's decision with its explanation, policy section and payout, once there is one.
- A typed API client with strict types, no `any`, and one test file per component.
- The brand in `docs/BRAND.md`: Instrument Sans, Instrument Serif for the tagline and JetBrains Mono for ids and amounts; Midnight ink on light, Sea Mist on dark, `#F0F4F5` and `#04232E` pages, rounded panels with no outline and no one-sided borders; states in Marigold (waiting), Cerulean (in progress), Coral (waiting for sign-off) and Sea Green (decided); money like $1,250.00; light and dark mode from the system setting.

Success criteria:

- [x] `npm run check:ts` passes with at least 80% coverage, with tests for the queue, the state counts, the claim view with and without a decision, and loading and error states.
- [x] In the browser (Playwright MCP) at `http://localhost:5173`: the queue lists 25 claims; the counts read 25 waiting and 0 for the rest; selecting `CLM-1001` shows its story, three items totalling $420.00, four documents and the Premium plan.
- [x] In the browser, a screenshot in light mode and one in dark mode show the brand's backgrounds, fonts and state colors, with readable text.
- [x] The page has no horizontal scroll at 1280 px and stays usable at 800 px.
- [x] No `any` in `dashboard/`, and `tsc` passes in strict mode.

## Phase 5: Dashboard, live work, sign-off and instructions

Adds the rest of what the team needs to see and do.

- Running the agent: a "Run agent" button on a waiting claim, which calls `run_agent`.
- The steps panel: what the agent is doing, step by step, polling every 2 seconds while the claim is in progress, and stopping when it is decided.
- Sign-off: on a claim waiting for sign-off, a handler picker (from `list_handlers`) and a "Sign off and pay" button. The payment and the handler's name then show on the claim.
- The Instructions view: the agent's instructions, read only.
- Playwright end-to-end tests using their own backend (port 8001) and dashboard (port 5174) with a throwaway database, so they never touch the dev data. The Vite proxy target becomes configurable. The old health test is replaced.

Success criteria:

- [x] `npm run check:ts` passes with at least 80% coverage, with tests for the run button, polling that starts and stops, the sign-off form and the instructions view.
- [x] `npm run e2e` passes, with tests that: set up a $1,250 approval through the API and watch the claim show "waiting for sign-off"; try the payment through the API and get the block; sign off as Dana Whitfield in the dashboard and see "decided" and a $1,250.00 payment; show seeded steps in order; show the instructions page and confirm it offers no way to edit them.
- [x] In the browser (Playwright MCP) against the dev servers, running a stubbed claim state by hand through the API shows the steps panel updating without a reload.
- [x] The e2e run leaves `backend/undercover.db` untouched.
- [x] `npm run check` passes in full.

## Phase 6: Live check and wrap-up

Runs the real model once, finishes the documents and does the final check. This uses your OpenRouter key and costs a few cents.

- Run the agent live on five claims chosen to span the work: one routine approval, one decline, one referral, one approval over $1,000 and one baggage claim with a deductible. Look at the decisions, steps and explanations in the dashboard.
- Fix any problem in the instructions or tools that this shows. Do not tune against the handlers' answer key; I will not open `data/evals/` unless you ask me to. Scoring against it is a separate step you can request.
- Write a short `README.md` for the product part: how to run, check and reset the database.
- Update `docs/STATUS.md`: what is built, how to run it, how the agent is started, the API and the open items.

Success criteria:

- [ ] `uv run -m agent CLM-1001` (with the real key) finishes, leaves the claim `decided` or `waiting_for_sign_off`, and the explanation is two or three sentences and names a policy section.
- [ ] Five live runs complete without a crash. Each shows its steps in the dashboard, and any approval over $1,000 stays `waiting_for_sign_off` with no payment.
- [ ] I list the five claims, the decisions and the payouts in my report, without comparing them to `data/evals/`.
- [ ] `docs/STATUS.md` describes the product as built and how to run it, and the root `README.md` is short and has no emojis.
- [ ] `npm run check` passes: ruff, pyright, pytest at 80% or more, ESLint, Prettier, tsc, vitest at 80% or more, and Playwright.
- [ ] `git status` is clean, every phase is committed with the pre-commit hook run, and nothing is pushed.

## Out of scope

As in the spec: the claims manual, sign-in, real payments, and messages to customers. Also not built: scoring against `data/evals/`, a payout calculator tool, and any change to the agent's harness. Those come in later builds.

## Questions for you

- Instructions in `backend/instructions.md` (my pick, since the agent must get them through the API) or somewhere else?
- Is a per-claim "Run agent" button enough in the dashboard, with `--all` on the command line for the whole queue, or do you want a "Run all waiting" button too?
- Should a handler's sign-off also make the payment (my pick), or should it only record the sign-off and leave the payment to a second step?
