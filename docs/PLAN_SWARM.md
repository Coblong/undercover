# Plan: a swarm of claim agents and a scoreboard

Builds `docs/SWARM.md`: a dispatcher with no model in place of the queue agent, so many claim agents work at once; dispatches and evaluations started from the dashboard; the tokens, cost and time of every claim; a Scoreboard page that scores the agent against the handlers' decisions; whole runs found together in Langfuse; and GPT-6 Luna as the default model. It follows `docs/SPEC.md`, `docs/TECH_STACK.md`, `docs/CODE_STANDARDS.md`, `docs/PROCESS.md` and `docs/BRAND.md`.

SIDENOTE: For consistency with the course, please read `labs/week2day4.ipynb` and build the dispatcher in the same style as the lab. Where the lab and this brief differ, follow the brief.

I read the lab. Its dispatcher is a `StateGraph` with a `Dispatch` state (`claims`, and `results` with the `operator.add` reducer), a `list_claims` node, a `fan_out` conditional edge that returns one `Send("work_claim", ...)` per claim, and a `work_claim` node. `max_concurrency` in the config caps the swarm, a `RetryPolicy` sits on the worker, and the claim agent's cost comes from `response_metadata["token_usage"]["cost"]`. Its scores are `decision_accuracy`, `accuracy`, the cost of mistakes (wrongful decline $1,500, needless referral $50, a wrongful approval its payout, a wrong amount the difference) and the straight-through rate. This build follows that shape. It differs where the brief does:

- Each claim keeps its own thread, so the dispatcher saves no progress of its own and collects no results for scoring. The backend's records are the results.
- The worker retries only rate limits and timeouts, and an `error_handler` leaves a claim in progress when the retries run out. Any other error fails the claim as today.
- The claims come from the backend, not from files, and the scores are worked out by the backend from its records.
- The dispatcher passes a run id, so each claim's cost, time and trace belong to a run.

## What exists

- **Backend:** FastAPI and SQLAlchemy Core on Supabase or SQLite. The queue agent (`agent/queue.py`) runs inside the backend process as a background task. It works claims one at a time through `work_claim`, which starts an attempt and calls the agent's service. `runs` has a claim limit and a summary, and `todos` holds the to-do list.
- **Agent's service** (`agent/service.py`, port 8002): `POST /claims/{claim_id}/work` runs the claim graph on a thread, on one `PostgresSaver` over a pool of 2 connections. The backend calls it for every attempt, for "Run agent" and after every sign-off.
- **Claim agent** (`agent/main.py`): the LangGraph graph, with the default model `deepseek/deepseek-v4.1-flash`. Its `agent` node retries on any error.
- **Dashboard:** a "Run the queue" panel with the to-do list, the queue, the claim, and an Instructions view that shows two sets of instructions.
- **Tests:** the end-to-end stand-in for the service (`e2e/agent.mjs`, port 8003) answers `/claims/{id}/work` only.
- **Packages:** LangGraph is 1.2.12. Nothing else is needed, except the change under decision 2.

## Design decisions

These shape every phase. Please review them first.

1. **How a dispatch runs.**
   - The dashboard calls the backend (`start_dispatch`). The backend asks the service through `backend/agent_service.py`, passing N. The service refuses with a 409 if a run is going. Otherwise it creates the dispatch through the backend (`create_dispatch`), answers 202 with its id, and works it on a background thread.
   - I read "answers at once" as "does not wait for the work". The service makes the one fast call to create the run before it answers, so the dashboard gets the run's id and the panel can follow it. The backend writes nothing before it asks.
   - Evaluations take the same path (`start_evaluation`, `create_evaluation`), with the set and `tiny` in the call.
   - `backend/agent_service.py` also takes over the existing call to `/claims/{id}/work` and its two stop exceptions from `backend/work.py`, so the backend has one place that talks to the service.
2. **The dispatcher** (`agent/dispatcher.py`, the fifth part of the agent) is a LangGraph graph with no model and no checkpointer.
   - **State:** `run_id`, `claims`, `round` and `results` (`operator.add`).
   - **Nodes:** `list_claims` asks the backend for the run's claims (`plan_run`) and a conditional edge fans out one `Send` per claim, in order. `work_claim` is the worker. A `check_again` node after the worker runs once the whole round has finished.
   - **Concurrency:** `max_concurrency` is N. I prototyped this in a scratch script. It never ran more than N at once and started claims in the order sent.
   - **Retry round:** `check_again` sends the claims that came back to `waiting` once more in the same dispatch. The backend's rule of two failed attempts, then left for a handler, is what stops it. A second round is never followed by a third.
   - **LangGraph version:** the brief needs `error_handler`, and 1.2.12 does not do the job. When several workers run at once, a failure the handler caught still raises when the step ends, which stops the dispatch. My prototype showed it, and showed it fixed in 1.2.19. Phase 2 raises the pin to `langgraph>=1.2.19` and runs the full checks. The pin is the one change outside the brief, and the brief cannot be met without it.
   - **Injection:** `dispatcher.build(work)` takes the worker's function, so tests give it a stand-in.
3. **The harness function.** `work_claim(claim_id, run_id, new_saver, llm)` in `agent/main.py` is the only place the dispatcher touches the claim agent, so later it can point at the agent in the cloud. In order, it:
   1. Takes the in-process lock for the claim. A claim the service is already working, such as one resuming after a sign-off, is skipped and reported as in progress.
   2. Asks the backend to start the claim in the run (`start_claim`). A waiting claim gets a new attempt on a new thread. A claim in progress carries on its open attempt on the same thread. The answer says whether the claim is an evaluation's copy.
   3. Runs the claim graph on that thread with a saver of its own (`new_saver()` builds one on the shared pool). `run` gets the run id, for cost, time and the Langfuse tag.
   4. Settles the claim (`end_attempt`) as today: no error ends it as decided or waiting for sign-off, and any other error fails the attempt. The first failure puts the claim back to `waiting` and the second leaves it for a handler.
   5. Passes `ModelRateLimitError` and `ModelTimeoutError` through without settling, so the attempt stays open.
   6. For an evaluation's copy that stopped at a held approval, deletes the thread's saved progress with `delete_thread`.
   - `main.run` also gets an optional run id, and the service's existing `/claims/{id}/work` route calls it with none.
4. **Retries and errors, in three layers.**
   - **A rate limit or a timeout:** `ChatOpenAI` retries a call first. If it still raises, the exception leaves `work_claim` and the worker's `RetryPolicy` (`retry_on` the two LangChain errors, a few attempts with growing waits) calls `work_claim` again. `start_claim` carries on the open attempt, and the graph carries on from its last saved step. That is the same attempt, with one decision, because the backend never decides or pays a claim twice.
   - **Retries run out:** the worker's `error_handler` records the claim as still in progress and logs why. The claim stays in progress with its open attempt, the dispatch goes on, and the next dispatch carries it on. The handler catches anything else the worker raises too, such as a backend that cannot be reached, and treats it as a stop.
   - **Any other error:** `work_claim` ends the attempt as failed and returns the claim's new state.
   - **Model settings:** `model()` gets `timeout=120` and `max_retries=5`, as in the lab, so a slow call surfaces as `ModelTimeoutError` in two minutes.
   - **A stopped claim is not a failed one:** if the service goes away, nothing is settled. The next dispatch carries the claim on.
   - **A 409 from the service is a stop.** Today the backend's `call_service` treats it as an error and would fail the attempt. With the dispatcher in the service, a sign-off and a dispatch can reach the same claim, and the in-process lock refuses the second. The backend now treats that 409 as a stop, and the winner settles the one open attempt.
5. **Connections.** Each process holds at most 4.

   | Process | Connections |
   |---|---|
   | Backend | 3, with 1 overflow, and a pool timeout of 60 seconds so a request waits for one |
   | Agent service | 4, and the pool's timeout is 120 seconds so a claim agent waits for one |
   | CLI | none |

   That is 8, inside the 12 the brief allows. Each claim's saver is a `PostgresSaver` of its own over the shared pool, because one saver holds a lock around every read and write and would make 25 claim agents take turns.
6. **Runs, copies and records.**
   - **`runs`:** a dispatch or an evaluation (`kind`). It holds N, the model, the reasoning effort (`none` or `low`), the state (`running`, `finished`, `failed` or `stopped`), and the set and `tiny` for an evaluation. The limit, the summary and the to-do list go. One running run at a time stays, as the unique index and a check.
   - **`run_claims`:** the claims of a run, in order, with when each started in the run and, for an evaluation, the claim each copy was made from.
   - **`model_calls`:** the tokens and the cost of each model call, with the claim and the run, or no run.
   - **`work_times`:** one row for each stretch of the agent's work on a claim, with its seconds and its run, or no run.
   - **`claims.evaluation_id`:** empty for the queue, and the evaluation for a copy. It is the one place a copy is told from a queue claim.
   - **`held_out_claims` and `answer_key`:** seeding stores the held-out claims and the handlers' decisions (set, claim, decision, payout, `tiny`) here, apart from every claim operation. Nothing but `backend/evaluations.py` and seeding reads them. A test checks that.
   - **Reseed needed.** The schema changes, so after pulling this the database is reset with `npm run seed`. A reset on SQLite now drops every table it finds, so the old `todos` table goes too. On Supabase it drops the schema, as it does today.
7. **Cost and time.**
   - **Cost:** the claim graph's `agent` node posts each reply's `usage_metadata` tokens and OpenRouter's `usage.cost` with the claim and the run. A call that raises records nothing, since it reports nothing.
   - **Time:** `run` times the graph's work, and leaves out the Langfuse flush and the fetch of the instructions. It records it in a `finally`, so a failed stretch counts. A graph paused at a sign-off returns at once, so the wait is never counted. The sign-off's own stretch is recorded later with no run.
   - **A killed process records nothing for the stretch it died in.** Everything it finished is recorded.
   - **Run totals** come from the tables by run id. A run's cost and time include its failed attempts, and a carried-on claim's cost is split between the runs that did the work.
8. **Evaluations.**
   - **The copies:** an evaluation copies its claims into `claims`, each with a new id of the form `EV7-CLM-1004` for evaluation 7, written into its own record. The whole set is copied, or just the five `tiny` claims. The documents never contain claim ids, so the id changes in one place.
   - **Copies never reach the queue:** `list_claims` leaves them out, and a dispatch's plan never lists them.
   - **Copies get the same world as the queue:** a copy's claim history is the customer's seeded claims plus the earlier-filed claims of the same evaluation, and a queue claim's history has no copies. The queue claim `CLM-1005` and its sibling `CLM-1006` have one customer, so the full set keeps that. The `tiny` claims have no sibling that matters; I checked.
   - **A held approval counts as finished:** a copy that pauses for a sign-off settles as `decided`. `needs_sign_off` stays true for it, so the graph pauses and does not try to pay. The harness then deletes its saved progress. `sign_off_claim` and `run_agent` refuse a copy, and the page shows no sign-off form and no "Run agent".
   - **A copy holds nothing from the handlers' decisions.** `get_claim` on a copy shows only what the agent did.
   - **A stopped evaluation shows as stopped.** The service marks every running run stopped when it starts.
9. **Scoring** is `backend/scoring.py`, pure functions the backend applies to its records when it reads an evaluation.
   - **Per claim:** the agent's decision, or a referral at $0 if it made none, against the handler's. The mistake cost is as in the lab.
   - **Measures:** `decision_accuracy`, `accuracy` (decision matches and payout within a dollar, compared after rounding to cents so a difference of exactly $1.00 never fails on a float), the cost of mistakes, the straight-through rate, model cost per claim, time per claim (the mean of each claim's work seconds in the run) and the time for the whole run. A stopped evaluation has no run time.
   - **The tests** work the measures out by hand for a small evaluation and compare, without calling the code under test to get the answer.
10. **The dispatch summary** is built when the backend reads a dispatch: counts of approved, declined and referred claims, each payout waiting for a sign-off with its amount, and each claim left for a handler or still in progress. No model writes it.
11. **Langfuse.** `tracing.config_for` adds `langfuse_tags: ["run-<id>"]` to the trace's metadata when a run id is given. The session stays the claim's thread. All 25 traces of a dispatch are found by the tag `run-<id>`.
12. **The service when it starts.** It waits for the backend (up to a minute, since `npm run dev` starts both together), then marks any running dispatch or evaluation stopped.
13. **What goes.** `agent/queue.py`, its middleware, `backend/instructions_queue.md`, `write_todos` with its table, its test and its end-to-end test, the run limit and its checks, the backend's queue-run code in `work.py` and `runs.py`, `uv run -m agent` with no claim id and `--limit`, and the queue agent's section of the Instructions page. `uv run -m agent CLM-1001` still works one claim.
14. **The default model.** `openai/gpt-6-luna` with `reasoning_effort="low"`. With `MODEL_NAME` set, that model runs with no reasoning effort, and a run records `none`. A function, `model_choice()`, gives the name and the effort to both `model()` and the run's record.

## The API

Every operation has an `operation_id`, a one-line docstring and no optional inputs. A value that is sometimes absent, such as a run id, is a required field that may be null.

| operation_id | Route | Used by |
|---|---|---|
| `start_dispatch` | `POST /api/dispatches` (`concurrency`) | dashboard |
| `start_evaluation` | `POST /api/evaluations` (`concurrency`, `eval_set`, `tiny`) | dashboard |
| `get_latest_dispatch` | `GET /api/dispatches/latest` | dashboard |
| `list_evaluations` | `GET /api/evaluations` | dashboard |
| `get_evaluation` | `GET /api/evaluations/{run_id}` | dashboard |
| `create_dispatch` | `POST /api/runs/dispatch` (`concurrency`, `model`, `reasoning_effort`) | service |
| `create_evaluation` | `POST /api/runs/evaluation` (those, and `eval_set`, `tiny`) | service |
| `plan_run` | `POST /api/runs/{run_id}/plan` | dispatcher |
| `start_claim` | `POST /api/runs/{run_id}/claims/{claim_id}/start` | harness |
| `end_attempt` | `POST /api/claims/{claim_id}/attempt/end` (`error`) | harness |
| `record_model_call` | `POST /api/claims/{claim_id}/model-calls` (`run_id`, tokens, `cost`) | claim agent |
| `record_work_time` | `POST /api/claims/{claim_id}/work-times` (`run_id`, `seconds`) | claim agent |
| `end_run` | `POST /api/runs/{run_id}/end` (`state`) | service |
| `stop_runs` | `POST /api/runs/stop` | service |

Removed: `start_run`, `get_latest_run`, `write_todos`, `work_claim` and `finish_run`. `get_claim` gains `evaluation_id`. `get_instructions` serves the claim agent's instructions only. The service gains `POST /dispatches` and `POST /evaluations`, and keeps `POST /claims/{claim_id}/work`.

## Phase 1: The default model, and cost and time for every claim

Builds the first item and the records the later phases total. It comes first because the dispatcher and the scoreboard both read cost and time.

- `model_choice()`, and Luna as the default, in `agent/main.py`.
- The `model_calls` and `work_times` tables, their models and the two `record_*` operations. A run id that does not exist is a 404, and a negative or non-finite cost, token count or time is a 422.
- `ClaimNodes` takes a run id. The `agent` node posts each reply's tokens and cost. `run` takes an optional run id, times the graph's work and records it in a `finally`.
- Run totals in the backend, for the later phases.
- I stage only my own files in each commit. Your local edits to `labs/week2day4.ipynb` stay out. `docs/SWARM.md` and this plan go into the first commit.

Success criteria:

- [x] `uv run pytest agent backend` passes, with tests that: the default is `openai/gpt-6-luna` with `low` effort and a run records that; `MODEL_NAME=openrouter/free` runs with no effort and records `none`.
- [x] A stub reply carrying tokens and a cost records one `model_calls` row with the claim and the run id, and a call that raises records none. A node retried until it succeeds records one.
- [x] Time is recorded when the graph raises. A claim that pauses for a sign-off and is resumed later has two rows, the second with no run, and a controlled clock shows the wait between them is in neither.
- [x] A run id that does not exist gets a 404, and a negative cost or time gets a 422.
- [x] `test_openapi.py` lists the new operations and still passes: every one has an `operation_id`, a one-line description and no optional input.
- [x] `npm run check:python` passes, including `types:check`, after `npm run types`.
- [x] `docs/STATUS.md` says the default model and what is recorded. The phase is committed with the pre-commit hook run.

## Phase 2: The dispatcher in place of the queue agent

Builds the dispatcher, the harness function and the backend operations behind them, and removes the queue agent. It comes second because starting from the dashboard needs something to start.

- Raise the pin to `langgraph>=1.2.19`, update `uv.lock`, and run the whole check.
- `runs` becomes dispatches and evaluations as in decision 6, with `run_claims`. `create_dispatch`, `plan_run`, `start_claim`, `end_attempt`, `end_run`, `stop_runs` and `get_latest_dispatch`, with its claims, cost, times and summary. A dispatch's plan is every claim in progress and every waiting claim, oldest submitted first (claim id breaks a tie), as one list, not in-progress claims first. I read the brief literally here.
- `agent/dispatcher.py` as in decision 2, and `main.work_claim` as in decision 3, with the in-process lock moved from the service into `main.py`.
- `model()` gets `timeout=120` and `max_retries=5`.
- Delete what decision 13 lists. The CLI now takes a claim id only. Existing tests for the removed pieces go with them, and the backend's tests for attempts, rules and recovery keep passing.
- The connection settings of decision 5, and the pool in the service built by a function a test can call without connecting.

Success criteria:

- [x] `uv lock` shows LangGraph at 1.2.19 or later, and `npm run check:python` is green.
- [x] Dispatcher tests, on a stand-in worker that sleeps: with N=3 and 10 claims the most ever working at once is 3, and with N=25 on 25 claims it is 25. Claims start in order, oldest submitted first, with claims in progress mixed in by date.
- [x] A claim that raises `ModelRateLimitError` once, through the real `work_claim` on a stub model, an in-memory saver and the in-process backend, carries on as the same attempt: one attempt on the claim, one thread, one decision, and the model is not asked again for a decision it already made.
- [x] A claim that keeps raising either error until the retries run out stays `in_progress` with its attempt open, no other claim is affected, and a second dispatch carries it on as the same attempt.
- [x] A claim that fails with any other error goes back to `waiting` and is worked once more in the same dispatch. A claim that fails twice is `left_for_handler`, and a claim with one earlier failure that fails again is left without a second round. One bad claim never stops the dispatch.
- [x] No claim is decided or paid twice in any of those tests, and the backend's own tests of the rules still pass.
- [x] A claim the service is already working is skipped by `work_claim`, and the service's route answers 409 for it. The backend treats that 409 as a stop, not a failed attempt.
- [x] A claim that pauses for a sign-off stays `waiting_for_sign_off`, no other claim waits for it, and its thread resumes after the sign-off.
- [x] A test of the saver factory shows a new saver is built for each claim. A test of the pool shows a maximum of 4 and a timeout of 120 seconds, and of the backend engine a pool timeout of 60.
- [x] `grep -rn "queue.py\|write_todos\|todos\|instructions_queue\|start_run" backend agent dashboard e2e` finds nothing but the docs' history, and `uv run -m agent` with no claim id prints its usage.
- [x] `npm run check` passes with the end-to-end test of the to-do list gone. The phase is committed.

## Phase 3: Starting a dispatch from the dashboard

Builds the service's start route, the backend's operation that asks it, and the run panel. It comes third because the dispatcher has to exist first.

- **Service:** `POST /dispatches` as in decision 1, with a guard so a second start is refused while a run is going, a daemon thread for the work, `end_run` when it finishes (`failed` if the graph raises), the saver factory over the shared pool, and the wait for the backend then `stop_runs` at start-up.
- **Backend:** `backend/agent_service.py`, `start_dispatch` (503 with "The agent's service could not be reached." when the service is not running, and the service's 409 passed through), and a dependency tests replace through the `agents` fixture.
- **Dashboard:** the "Run the queue" panel becomes a dispatch panel. It has a number of claim agents from 1 to 25 with 3 to start with and a start button, disabled while a run is going. It shows the latest dispatch: its state, N, model cost, elapsed time and the sum of its claims' times. It lists each claim with where it stands, its decision and payout, its start time, time and cost, and it polls every 2 seconds while running. When the dispatch ends it shows the summary. The Instructions page shows the claim agent's instructions only.
- **End-to-end:** the stand-in on port 8003 answers `POST /dispatches` and `POST /evaluations` as the service would. It records the call with N, creates the run through the backend, and passes a 409 through.

Success criteria:

- [x] Service tests, with an in-memory saver and the in-process backend: a start answers 202 with the run's id and creates a `running` dispatch with N, the model and the effort; a second start while the first runs answers 409 and creates nothing; the run ends `finished` when the work does, and `failed` when the graph raises; starting the service marks a running dispatch `stopped` and leaves finished ones alone.
- [x] Backend tests: `start_dispatch` passes N to the service, returns the dispatch, answers 503 with that message when the service is unreachable, and passes a 409 through. A second start is refused while one runs.
- [x] The summary is built from the records in a test: counts of approved, declined and referred, a held payout with its amount, a claim left for a handler and one in progress.
- [x] `get_latest_dispatch` totals cost and time by run: a dispatch's totals are its own, include a failed attempt, and leave out a sign-off's later stretch, which has no run.
- [x] `npm run check:ts` passes with 80% coverage and tests for the panel: N from 1 to 25 and 3 to start, the refused start showing the backend's reason, the claims list, polling that stops when the run ends, the summary, and a start disabled while running.
- [x] `npm run e2e` passes with tests that starting a dispatch on the dashboard with N=7 reaches the stand-in with N=7, that a second start shows the refusal, and that a dispatch played out through the API shows its claims, cost, time and summary.
- [x] `npm run check` passes. The phase is committed.

## Phase 4: Evaluations

Builds the answer key, the copies, their rules and the scoring in the backend and the service. It comes fourth because it uses the dispatcher unchanged.

- **Seeding:** `held_out_claims` and `answer_key` from the three files in `data/evals/` that the brief allows (`decisions.json`, `held_out_claims.json`, `held_out_decisions.json`). The test of which files the backend reads adds those three and no others. I open them only through seeding. The `PROCESS.md` rule not to open `data/evals/` stays, and this brief is the ask.
- **`create_evaluation`:** copies the claims and records the run and its claims as in decision 8. `evaluation_id` on `claims`, the history rule, the `decided` settle for a held approval, and the refusals on a copy. `plan_run` returns a copy's claims in order.
- **Harness:** a held approval's thread is deleted after a copy's attempt.
- **`backend/scoring.py` and `backend/evaluations.py`:** the measures, and `list_evaluations` and `get_evaluation`, newest first, each claim with the handler's decision and payout beside the agent's, its cost and its time.
- **Service and backend:** `POST /evaluations` and `start_evaluation`, as for a dispatch.

Success criteria:

- [x] Seed tests: the 25 held-out claims are in `held_out_claims` and not in `claims`, `answer_key` has 25 rows for each set with the `tiny` flags, and the queue still has 25 claims. Only the three allowed files are read.
- [x] A test fails if any module but `evaluations.py`, `db.py` (seeding) and `tables.py` mentions `answer_key` or `held_out_claims`.
- [x] An evaluation of the five `tiny` claims of the held-out set makes five copies with new ids, `list_claims` is unchanged, a dispatch's plan never lists a copy, and `get_claim` on a copy has no field from the handlers' decisions.
- [x] A copy's claim history shows only its own evaluation's earlier claims, and a queue claim's history shows no copy. `CLM-1005` and `CLM-1006` still see each other in an evaluation of the whole set.
- [x] A copy approved over $1,000 ends `decided` with its payment unmade, its thread's saved progress is deleted, and `sign_off_claim` and `run_agent` refuse it with a plain reason.
- [x] A stopped evaluation shows `stopped`, and its undecided claims count as referrals.
- [x] The measures for a small evaluation, worked by hand in the test, match: a right approval within $1.00, a payout $0.99 off, a wrongful decline, a wrongful approval that was never paid, a needless referral, an undecided claim, and a wrong amount on a right approval. The test covers `decision_accuracy`, `accuracy`, the cost of mistakes, the straight-through rate, cost per claim, time per claim and the run's time.
- [x] Each evaluation records its set, N, the model and the effort (`none` for `MODEL_NAME`), and for each claim the decision, payout, reason, cost and time. A dispatch's totals and an evaluation's stay separate when claims share a customer.
- [x] `start_evaluation` passes N, the set and `tiny` to the service and passes refusals through, and the service's start route behaves as the dispatch's does.
- [x] `npm run check:python` passes at 80% coverage and `npm run e2e` still passes. The phase is committed.

## Phase 5: The Scoreboard page

Builds the page that starts and shows evaluations. It comes last of the builds because it shows everything before it.

- A third view, Scoreboard, in the header. Its start form has the set (the evaluation set or the held-out set), a "just the five tiny claims" choice and N from 1 to 25 with 3 to start with.
- The evaluations, newest first, in a table that scrolls inside its panel: set, N, model, effort, state and every measure. Percentages are whole numbers, and cost per claim shows four decimals, since a claim costs fractions of a cent.
- An evaluation opens to its claims: the handler's decision and payout beside the agent's, a miss marked with the word "Miss" and the brand's Raspberry so color is not the only sign, and each claim linked to its page. A copy's page shows an "Evaluation copy" label, no sign-off form and no "Run agent". With no router in the app, the link opens the claim in the Claims view by its id.
- Polling every 2 seconds while an evaluation runs.

Success criteria:

- [ ] `npm run check:ts` passes with 80% coverage and tests for: the start form and its limits, a refused start, the list with every measure, the opened evaluation with a miss marked, the link opening the copy, the copy's page with no sign-off form and no "Run agent", polling that stops when the run ends, and loading and error states.
- [ ] `npm run e2e` passes with tests that: starting an evaluation of the held-out set with N=5 and `tiny` reaches the stand-in with 5, `held_out` and `tiny`; and an evaluation the test plays out through the API, as `run.spec.ts` does with a run today, shows on the scoreboard with measures that match ones the test worked out, with a miss marked and its claim's page opened from the link.
- [ ] The queue page's counts are 25 waiting before and after that evaluation in the same test.
- [ ] In the browser (Playwright MCP) against the end-to-end servers' data, the Scoreboard shows in light and dark mode with the brand's fonts and colors, and the page has no horizontal scroll at 1280 px and stays usable at 800 px.
- [ ] No `any` in `dashboard/`, and `tsc` passes in strict mode. `npm run check` passes. The phase is committed.

## Phase 6: Live check and documents

Runs the real model on the runs the brief lists, then brings the documents up to date. It uses the OpenRouter key and costs roughly four passes of the queue. I run `npm run seed` once first, and keep to these runs only.

1. `npm run seed` once, then `npm run dev`.
2. **N=3, stopped.** Start a dispatch at N=3 from the dashboard. Partway through, `kill -9` every process on port 8002 (`lsof -ti tcp:8002`; on Windows `Get-NetTCPConnection -LocalPort 8002 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`). `npm run dev` stops the others. Start it again.
3. **N=25.** Start a dispatch at N=25. It carries on the claims left in progress as the same attempts and works the rest 25 at once. Meanwhile sign off a held claim in the browser.
4. **Two evaluations.** The evaluation set at N=25, and the held-out set's five `tiny` claims at N=5.
5. **Documents:** `SPEC.md` (the sections on working the queue and what the team needs to see; Rules untouched), `TECH_STACK.md`, `PROCESS.md` (these runs replace the five-claim cap), `CODE_STANDARDS.md` and `STATUS.md`.

Success criteria:

- [ ] After the kill, the claims the N=3 dispatch left in progress show one open attempt each, and `npm run dev` started again marks that dispatch `stopped`.
- [ ] The N=25 dispatch's claims in progress carry on as the same attempts: each still has one attempt and the same thread. The start times of the claims it worked from the start fall within a few seconds of each other, so 25 ran at once.
- [ ] The N=25 run takes well under the sum of its claims' times, and I report both.
- [ ] Compared with the claims of the N=3 dispatch, each claim worked from the start takes about as long. If they are slower, I report where the time goes, keeping to 4 connections.
- [ ] A claim held for a sign-off waits while the dispatch runs, and a sign-off made during it pays within five seconds.
- [ ] No claim fails for want of a database connection: no step or log mentions a pool timeout. Through the Supabase MCP server, `pg_stat_activity` for the `postgres` user never shows more than 12 connections while the N=25 dispatch works, sampled throughout.
- [ ] The N=25 dispatch's panel shows each claim, its model cost and time, and the summary built from the records.
- [ ] Both evaluations appear on the Scoreboard with every measure, and the queue page's counts are the same before and after them.
- [ ] Through Langfuse's API (`GET /api/public/v2/observations`, as `docs/STATUS.md` describes), the claims of the N=25 dispatch are found together by its tag `run-<id>`, one trace group for each claim's session.
- [ ] Screenshots of the dispatch panel and the Scoreboard in light and dark mode show readable text and the brand's colors.
- [ ] I report the evaluations' measures as they are, without tuning anything against `data/evals/`.
- [ ] The five documents describe the product as it now is. `STATUS.md` says how to run a dispatch and an evaluation, what is recorded, the reseed, the live check and its results, and the open items.
- [ ] `npm run check` passes with `.env` moved out of the way, and the results are reported. `git status` is clean, every phase is committed with the hook run, and nothing is pushed.

## Out of scope

As the brief says: the claim agent's graph, tools and instructions apart from cost, time and the default model; the rules in `docs/SPEC.md`; sign-in; real payments; and messages to customers. Not built: a router in the dashboard, a way to stop a running dispatch from the dashboard, returning a claim left for a handler to the queue, and pointing the dispatcher at the agent in the cloud, which `work_claim` is shaped for.

## Questions for you

- **LangGraph 1.2.19.** The brief's `error_handler` cannot keep a dispatch going in 1.2.12, which the repository pins, so I raise the pin. If you would rather not change a dependency, the alternative is to catch the error inside the worker instead of using `error_handler`. That departs from the brief's wording.
- **`docs/CODE_STANDARDS.md` has six rules.** The brief names rules 10 and 13 and "the four parts" of the agent, and neither is there. I read the intent as saying the agent has five parts, and that each process holds at most 4 connections and claim agents wait for one. I will add those as rules 7 and 8, and list the parts in `TECH_STACK.md`. If you have a longer version of the file, tell me.
- **The Langfuse tag.** I use `run-<id>` for dispatches and evaluations alike, since they share ids. Say if you want the kind in it, such as `dispatch-12`.
- **Copy ids.** `EV7-CLM-1004` shows which claim a copy was made from, and the handlers' decision is stored apart, so that leaks nothing. Say if you want an id that hides the source.
