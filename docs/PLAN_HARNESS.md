# Plan: the harness

Builds `docs/HARNESS.md`: the UnderCover Agent organizes its own work. One run works the queue from a to-do list, hands each claim to a fresh subagent, looks up the policy wording and claims manual section by section, and recovers from failures. It follows `docs/SPEC.md`, `docs/TECH_STACK.md`, `docs/CODE_STANDARDS.md`, `docs/PROCESS.md` and `docs/BRAND.md`. `docs/PLAN.md` stays as it is.

## What exists

The pilot (`docs/STATUS.md`). The backend starts one agent per claim, in a background thread. The agent has six tools, is handed the whole policy wording, and posts its steps to the backend. A run that crashes puts the claim back to `waiting`, with no limit on how often. The dashboard has a Claims view (queue and claim) and an Instructions view. `agent/` already uses LangChain's `create_agent`, which has what this build needs (middleware for retries and call limits). No new dependencies, and no other agent framework: `deepagents` is installed but I will not use it, as the brief asks for these pieces to be built into our own agent.

## Design decisions

These shape every phase. Please review them first.

1. **Two agents, both in `agent/`.**
   - The **queue agent** runs the queue. It has four tools: `list_waiting_claims`, `write_todos`, `work_claim` and `finish_run`. It never reads a claim itself, so its context stays small however long the run is.
   - The **claim agent** is a fresh `create_agent` for every `work_claim` call, with a new thread id and no shared history. It is today's agent, changed: it looks documents up by section, and it tells the queue agent nothing except through the claim's recorded result.
2. **The backend runs the bookkeeping, as in the pilot.** `work_claim` is one backend operation: `POST /api/runs/{run_id}/claims/{claim_id}/work`. It starts an attempt on the claim, runs the claim agent, ends the attempt and returns one line built from the database, not from what the model said: the decision and payout, or what went wrong. The single-claim "Run agent" button uses the same code without a run, so there is one path for starting, ending and retrying a claim.
3. **Attempts and retries are rules in the backend.**
   - A new `attempts` table records each try on a claim: the run it belonged to (if any), when, and whether it failed.
   - An attempt fails if the agent crashes, or finishes without a decision. A claim that fails with no decision goes back to `waiting`. On its second failed attempt it is left for a handler.
   - A claim that was decided but is an approval of $1,000 or less with no payment (the agent failed before paying) also counts as unfinished: it goes back to `waiting`, and the claim agent is told to pay it, not decide it again. `decide_claim` and `pay_claim` already refuse a second decision or payment with a 409, so nothing is decided or paid twice.
   - On backend start, a claim left `in_progress` and a run left `running` (the process died) are settled the same way, as a failed attempt. Nothing stays stuck.
4. **A new claim state, `left_for_handler`** (label "Left for a handler"). Four states cannot show a claim the agent has given up on, and recording a made-up "refer" decision would be a decision nobody made. The agent cannot move a claim out of it; a handler's way to pick these up is not part of this build.
5. **Runs, the cap and the missed-claim check live in the backend.**
   - `POST /api/runs` takes a required `limit` and starts the queue agent in the background. A run counts the claims it has started. Starting a new claim past the limit is refused with a 409 the agent can read. A retry of a claim already started in the run does not count again.
   - `finish_run` takes the agent's summary and is refused with a 409, listing the claims, while the run has missed work: waiting claims it could still start under its limit, or claims waiting for a retry. When nothing is missed, it ends the run. This check cannot be skipped by the model. If the agent stops without finishing, the runner reminds it up to twice, then ends the run as failed.
6. **The to-do list is stored.** `write_todos` replaces the run's list, as LangChain's own to-do tool does, but stores it through the backend so the dashboard can show it. Each item has text and a status: `to_do`, `in_progress`, `done` or `left_for_handler`. The dashboard shows the latest run's list.
7. **Looking things up.** The backend loads `claims_manual.md` (it still never loads `data/evals/`, `data/security/` or `knowledge.json`). Two operations serve both documents by section: `list_document_sections` and `get_document_section`, each with a required `document` of `wording` or `manual`. Sections are the `## N. Title` headings. The whole-wording operation and tool are removed. The claim agent reads a table of contents, then the sections it needs. Each section the agent reads is recorded as a step of a new kind, `read`, which the claim page lists as "What it read".
8. **Failures inside a claim.** The claim agent uses LangChain's `ModelRetryMiddleware` (provider errors and timeouts) and `ToolRetryMiddleware` (a tool that fails comes back to the model as text instead of crashing) and a `ModelCallLimitMiddleware` so one claim cannot loop forever. A claim that still fails is the attempt rule in decision 3.
9. **The model.** `deepseek/deepseek-v4.1-flash` with low reasoning effort is the default for both agents. `MODEL_NAME` overrides it, with no reasoning setting, as now.
10. **Instructions.** Two files, `backend/instructions_queue.md` and `backend/instructions_claim.md`, replace `backend/instructions.md`. They are written from the policy wording and the manual only. I will not open `data/evals/`. `get_instructions` takes a required `agent` of `queue` or `claim`.
11. **Storage.** The schema gains tables, so the dev database must be reseeded: stop the backend, delete `backend/undercover.db`, start it again. Tests and e2e already use their own database.
12. **Tests never call a model.** Each new behavior is tested with stub models. The queue agent and each claim agent get their own scripted stub, injected where the model is built.

## The API

New and changed operations. Each has an `operation_id`, a one-line docstring and no optional inputs.

| operation_id | Route | Used by |
|---|---|---|
| `list_document_sections` | `GET /api/documents/{document}/sections` | claim agent |
| `get_document_section` | `GET /api/documents/{document}/sections/{number}` | claim agent |
| `get_instructions` (changed) | `GET /api/instructions/{agent}` | dashboard, agents |
| `start_run` | `POST /api/runs` with `limit` | dashboard, CLI |
| `get_latest_run` | `GET /api/runs/latest` | dashboard |
| `write_todos` | `PUT /api/runs/{run_id}/todos` | queue agent |
| `work_claim` | `POST /api/runs/{run_id}/claims/{claim_id}/work` | queue agent |
| `finish_run` | `POST /api/runs/{run_id}/finish` with `summary` | queue agent |
| `run_agent` (kept) | `POST /api/claims/{claim_id}/run` | dashboard, CLI |

`get_policy_wording` is removed. `list_claims` and `get_claim` gain the new state.

## Phase 1: The claim agent looks things up

Gives the claim agent the manual and section-by-section lookup, the new default model and its own failure handling. Done first because everything after it hands claims to this agent.

- Backend: load `claims_manual.md`; parse both documents into sections; `list_document_sections`, `get_document_section`; remove `get_policy_wording`.
- `backend/instructions_claim.md`, written from the wording and the manual (the manual's section 4 order of work, section 5 documents, section 6 payout, and the topics it covers: repeat claims, meals without receipts, wild animals, SIU referrals). It tells the agent to list the sections of both documents and read what the claim needs, and to pay, not decide again, a claim that is already decided.
- `get_instructions` takes `agent`; the dashboard's Instructions view shows both (full dashboard changes come in Phase 5, but the view keeps working here).
- Agent: replace `get_policy_wording` with `list_document_sections` and `get_document_section`; the step reporter adds a `read` step for each section read; middleware for retries and the call limit; default model changed.

Success criteria:

- [x] `uv run pytest backend agent` passes, with tests for: the section list of each document (12 wording sections, 14 manual sections), one section's text, a 404 for an unknown section and a 422 for an unknown document, `get_instructions` for both agents.
- [x] The seed test is updated: the backend reads `claims_manual.md` and still never opens `data/evals/`, `data/security/` or `knowledge.json`.
- [x] A claim-agent test on a stub model reads two sections and `list_document_sections`, and the claim's steps include two `read` steps naming the sections, in order.
- [x] A test shows a tool that raises comes back to the stub model as text, and a model that keeps calling tools is stopped by the call limit and leaves the claim `waiting`.
- [x] A test shows the default model is `deepseek/deepseek-v4.1-flash` with low reasoning, and `MODEL_NAME` overrides it.
- [x] `GET /openapi.json` check passes: every operation has an `operation_id`, a docstring and no optional input.
- [x] No tool or code in `agent/` reads files under `data/` or `backend/`, as the pilot's test asserts.
- [x] `npm run check:python` passes.

## Phase 2: Attempts, retries and recovery

Makes a failed claim go back, try again once, and then be left for a handler, and makes sure nothing stays stuck. Builds on Phase 1's single-claim path.

- The `attempts` table, the `left_for_handler` state, and the failure rules in design decision 3, in the code that ends an attempt. The pilot calls that function `finish_run`; I will rename it `end_attempt`, because Phase 3 adds a run-level `finish_run` operation.
- Recovery at startup of claims left `in_progress`.
- `list_claims`, the models and the TypeScript state type know the new state.

Success criteria:

- [ ] Tests pass for: a crash with no decision returns the claim to `waiting` with one failed attempt; a second failed attempt leaves it `left_for_handler`; a run that finishes without deciding counts as failed; an approval of $420 with no payment goes back to `waiting`, and a retry on a stub model that only pays ends `decided` with exactly one decision and one payment.
- [ ] A test shows a left-for-handler claim cannot be started again (409) and cannot be decided or paid.
- [ ] A test shows startup recovery: a database with an `in_progress` claim, opened with the app, has the claim back in `waiting` with a failed attempt and an error step.
- [ ] A test shows an approval over $1,000 that fails after deciding settles to `waiting_for_sign_off`, not `waiting`.
- [ ] With the backend running and a stubbed failing runner, `POST /api/claims/CLM-1001/run` twice leaves `CLM-1001` as `left_for_handler`, checked with `GET /api/claims/CLM-1001`.
- [ ] `npm run check:python` passes with at least 80% coverage.

## Phase 3: A run, its to-do list and the cap

Builds the backend half of the queue run: the run record, the stored to-do list, the cap and the missed-claim check.

- `runs` and `todos` tables; `start_run`, `get_latest_run`, `write_todos`, `work_claim`, `finish_run` as in the API table.
- `start_run` creates the run and starts the queue agent in the background. In this phase the agent is a stub function; Phase 4 supplies the real one.
- `work_claim` starts an attempt tied to the run, runs the claim agent, ends the attempt, and returns the one-line result. It enforces the cap.
- `finish_run` makes the missed-claim check and ends the run with the summary.

Success criteria:

- [ ] Tests pass for: a run with `limit` 2 started on 25 waiting claims lets two claims start and refuses a third with a 409; the other 23 stay `waiting`.
- [ ] A test shows a claim that failed once in a run can be started again in that run without using up the cap.
- [ ] A test shows `finish_run` is refused, naming the claims, when a waiting claim could still be started under the limit, and when a claim is waiting for a retry; and is accepted when neither holds, including when the limit is reached with other claims still waiting.
- [ ] A test shows `write_todos` replaces the list, keeps the order, and rejects a status that is not one of the four (422); `get_latest_run` returns the limit, state, summary and items.
- [ ] A test shows a run that was `running` when the backend started is ended as failed.
- [ ] A test shows one failing claim does not stop `work_claim` on the next claim: the result line says what went wrong and the run continues.
- [ ] `npm run check:python` passes.

## Phase 4: The queue agent and the command line

Builds the queue agent and ties it to the run from Phase 3.

- `agent/queue.py`: the four tools, `run_queue(run_id)` and the runner's reminders; `agent/main.py` keeps the claim agent. The queue agent is a `create_agent` with the default model.
- `backend/instructions_queue.md`: plan a to-do list first, with an item per claim you mean to work, in the order you choose (for example smaller, simpler claims first, repeat claims and large amounts with care); keep it up to date; hand each claim to `work_claim`; read the one-line result and mark the item `done` or `left_for_handler`; before finishing, call `finish_run` with a short summary for the handlers, and if it is refused, work what it names.
- The command line: `uv run -m agent` works every waiting claim, `uv run -m agent --limit 5` works up to five, `uv run -m agent CLM-1001` works one claim. All print the outcome of each claim and the run's summary.

Success criteria:

- [ ] `uv run pytest agent` passes, with a scripted queue agent and scripted claim agents on stub models. Tests cover: a run on three claims writes a to-do list, works the claims in the order the agent chose, marks them done and finishes with a summary; the list in the database changes as the run goes.
- [ ] A test for the cap: `limit` 2 on five claims ends with two worked and three `waiting`.
- [ ] A test for a failed claim: the second of three claims crashes; the run still works the third; the failed claim is `waiting`, is retried in the same run and, on a second crash, ends `left_for_handler`; the to-do item is `left_for_handler`.
- [ ] A test for the check: a stub queue agent that tries to finish early is refused by `finish_run`, works the claim it names and finishes.
- [ ] A test that the agent stopping without finishing gets reminded, and after two reminders the run ends as failed with an error recorded.
- [ ] Each claim's steps are recorded on that claim, and the queue agent's own messages never include a claim's story or documents (checked by a test on its messages).
- [ ] `uv run -m agent --help` prints the new usage.
- [ ] `npm run check:python` passes.

## Phase 5: Dashboard

Shows the run, its to-do list and what the agent read. Uses the brand: Raspberry for "Left for a handler", and Marigold, Cerulean, Sea Green and Raspberry for the four to-do statuses.

- Queue page: a "Run the queue" form (a number of claims, filled in with the waiting count, and a button) and a panel for the latest run: its limit, its state, the to-do list with a status chip on each item, and the summary once it finishes. It refreshes every 2 seconds while the run is going.
- Counts: five states, including "Left for a handler".
- Claim page: a "What it read" list, from the `read` steps, above the steps panel; the steps panel shows `read` steps. "Run agent" stays for a waiting claim.
- Instructions view: both sets of instructions, each read only under its own heading.
- Playwright: set up a run, to-do items and steps through the API and check the dashboard. The e2e tests never run an agent.

Success criteria:

- [ ] `npm run check:ts` passes with at least 80% coverage, with tests for the run form, the to-do panel (each status, polling that starts and stops, the finished summary, no run yet), the five counts, the "What it read" list and the two instructions.
- [ ] `npm run e2e` passes, with tests that: start a run through the API with a limit of 5, write a to-do list, and see it in the dashboard; update an item and see the chip change without a reload; show a `left_for_handler` claim in the queue with the right label and no "Run agent" button; show a claim's "What it read" from seeded `read` steps; show both instructions with no way to edit them.
- [ ] In the browser (Playwright MCP) against the dev servers, a screenshot in light mode and one in dark mode of the queue page with a to-do list show the brand's colors with readable text, and the page has no horizontal scroll at 1280 px and stays usable at 800 px.
- [ ] No `any` in `dashboard/`, and `tsc` passes in strict mode.
- [ ] The e2e run leaves `backend/undercover.db` untouched.

## Phase 6: Live check and documents

Runs the real model once, updates the documents and does the final check. This uses your OpenRouter key and costs a few cents.

- Reseed (stop the backend, delete `backend/undercover.db`, start it), then run `uv run -m agent --limit 5`. Check in the dashboard: the to-do list filling in and being ticked off, each claim's steps and "What it read", and that the other 20 claims stay `waiting`.
- Fix problems this shows in the instructions or tools. I will not tune against `data/evals/`, and will not open it unless you ask.
- `docs/SPEC.md`: the manual is no longer "Not for now"; the agent also works the whole queue, from a to-do list. `docs/TECH_STACK.md`: the queue and claim agents, the default model. `docs/STATUS.md`: what is built, how to run and reseed it, the new API, the checked-live results, and the open items.

Success criteria:

- [ ] After reseeding, `uv run -m agent --limit 5` finishes without a crash, five claims have a decision or a recorded failure, and 20 claims are still `waiting`.
- [ ] Any approval over $1,000 in the run is `waiting_for_sign_off` with no payment; no claim has two decisions or two payments (checked with the API).
- [ ] In the dashboard (Playwright MCP) the to-do list and the summary are shown, and one claim's page shows what it read.
- [ ] My report lists the five claims, the decisions and payouts, the steps taken, and any retries, without comparing them to `data/evals/`.
- [ ] `docs/SPEC.md`, `docs/TECH_STACK.md` and `docs/STATUS.md` describe the product as it now is.
- [ ] `npm run check` passes in full: ruff, pyright, pytest at 80% or more, ESLint, Prettier, tsc, vitest at 80% or more, and Playwright.
- [ ] `git status` is clean, every phase is committed with the pre-commit hook run, and nothing is pushed.

## Out of scope

As the brief says: the rules in `docs/SPEC.md` stay, and no other agent framework is added. Also not built: scoring against `data/evals/`, any fix for payout arithmetic (the open item in `docs/STATUS.md`), working several claims at the same time, a way for a handler to pick up a left-for-handler claim, and sign-in, real payments and messages to customers.

## Questions for you

- A new claim state, `left_for_handler`, for a claim that has failed twice (my pick), or a "refer" decision recorded by the system so the existing four states are enough?
- A claim that is approved for $1,000 or less but unpaid when the agent fails goes back to the queue to be paid (my pick, since an unpaid approval breaks the spec). Or should it be left as decided and unpaid?
- `work_claim` as one backend operation that runs the claim agent (my pick, so the bookkeeping is in one place), or as a tool in `agent/` that starts and ends the attempt over HTTP?
- A run's cap is a required number. For "the whole queue" the command line and the dashboard fill in the number of waiting claims. Is that fine, or do you want an explicit "no cap" option?
