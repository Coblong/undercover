# Plan: approvals that wait, and work that survives a restart

Builds `docs/DURABLE.md`: the payout rules in the backend, Langfuse tracing, the backend's records on Supabase, a claim agent that waits for a sign-off and carries on, and work that survives a restart. It follows `docs/SPEC.md`, `docs/TECH_STACK.md`, `docs/CODE_STANDARDS.md`, `docs/PROCESS.md` and `docs/BRAND.md`.

SIDENOTE: For consistency with the course, please read `labs/week2day3.ipynb` and build the solution in the same style as the lab. Where the lab and this brief differ, follow the brief.

I read the lab. It builds the graph from `ClaimState`, an `agent` node, a `ToolNode`, a `decide` node, an `approval` node that calls `interrupt()` and a `pay` node that cannot pay twice. It checkpoints with `PostgresSaver` in a schema of its own, calls the graph with `durability="sync"`, uses the claim's thread id, and resumes with `Command(resume=...)`. This build follows that shape. It differs where the brief does:

- The claim agent keeps its `decide_claim` tool, so there is no separate `decide` node.
- The backend decides whether a sign-off is needed, not the graph.
- Payments go through the backend, not a table the graph writes.
- Progress and records share one Supabase database, in the `undercover` schema.

## What exists

- **Backend:** FastAPI and SQLite through the standard library's `sqlite3`, with the schema in `schema.sql`. Writes that read first run in `BEGIN IMMEDIATE`. A sign-off makes the payment.
- **Claim agent:** `create_agent` with seven tools, including `pay_claim`, and retry and call-limit middleware. The backend runs it in its own process, in a background thread.
- **Recovery:** on start, the backend fails any attempt left open and any run left going.
- **Queue agent:** `create_agent` with four tools. It stays as it is.
- **Not in the product yet:** the payout rules (`labs/week2day1_payout.py`) and Langfuse tracing.
- **Packages:** `langgraph-checkpoint-postgres`, `psycopg` and `langfuse` are installed. `sqlalchemy` is only in the non-default `server` group, so it moves into the main dependencies.

## Design decisions

These shape every phase. Please review them first.

1. **Who runs the claim agent.** The agent's service runs the claim graph, because the brief wants the agent to run as a service like this in the cloud. It is the only process that builds the graph, holds the checkpointer and traces.
   - The backend keeps what it does today: attempts, states, retries and recovery. It calls the service on `POST {UNDERCOVER_AGENT}/claims/{claim_id}/work` with `{thread_id}`. The service starts the thread, resumes it, or does nothing if it is finished, and answers with one line.
   - The backend's `work.claim_agent` dependency becomes that HTTP call. Tests keep replacing it with a plain function through the same override.
   - The queue agent stays in the backend process. Its `work_claim` tool calls the backend, which calls the service.
   - The service is the fourth part of the agent: `agent/service.py` beside `queue.py`, `main.py` (the claim agent) and `__main__.py` (the command line). `docs/CODE_STANDARDS.md` lists no parts, so I read "the three" as those.
2. **The graph.** `agent/main.py` becomes a hand-written LangGraph graph.
   - **State:** `messages`, `claim_id`, `model_calls`, `approved_by`.
   - **Nodes:**
     - `agent` calls the model, with the 30-call cap.
     - `tools` is a `ToolNode` that returns refusals to the model as text.
     - `check` reads the claim from the backend and routes.
     - `approval` calls `interrupt()`.
     - `pay` calls the backend's `pay_claim`.
   - **Edges:**
     - `START -> check`. A thread started on a claim that already has a decision goes straight on to pay it. This replaces the old "if it already has a decision, pay it" instruction, and it is how a claim that failed after deciding recovers.
     - `check -> agent` if there is no decision.
     - `check -> END` for a decline or a referral.
     - `check -> approval` if the backend says the claim needs a sign-off.
     - `check -> pay` for an approval that is already payable.
     - `agent -> tools` if the model asked for tools, otherwise `agent -> END`.
     - `tools -> check`.
     - `approval -> pay`, and `pay -> END`.
   - **Checks and retries:** the graph never compares a payout with the limit. `check` reads `needs_sign_off` from the claim. Model and tool failures use LangGraph's `RetryPolicy` in place of the retry middleware.
   - **Steps:** each node records its steps on the claim as it goes, in place of the old streaming loop. `steps_of` stays.
   - **Tools:** `calculate_payout` joins the six read and decide tools. `pay_claim` leaves them.
3. **Stopped is not failed.** This is the one change to the retry rules.
   - **An error** (the graph raises, the backend refuses the attempt) ends the attempt as it does today. The claim goes back to `waiting`, the second failure leaves it for a handler, and the next attempt starts a fresh thread.
   - **A stop** (the process dies, or the service cannot be reached) ends nothing. The attempt stays open on its thread and the claim stays `in_progress`. The backend no longer fails open attempts when it starts.
   - **The next run** (`uv run -m agent`, "Run the queue" or "Run agent") picks up every `in_progress` claim that no process is working, from its attempt's saved thread. The service carries on from the last checkpoint.
   - **A stopped queue run** stays `running`. The next `start_run` takes it over, with its limit, to-do list and counted claims, and finishes it. It does not start a second one.
   - **How the backend knows no process is working:** it keeps an in-memory set of the claims and runs it is executing. After a restart the set is empty, so everything left open is a stop. A run that is truly live still gets the 409.
4. **A sign-off.** `sign_off_claim` saves the sign-off and moves the claim to `in_progress`. It opens a resume attempt on the same thread and answers. A background task then calls the service and does not wait for the payment.
   - **If the service cannot be reached,** that is a stop. The sign-off stands, the claim stays in progress, and the next run picks it up.
   - **The payment** copies the sign-off's handler and time onto the payment row. The attempt ends after the graph pays, and the claim settles to `decided`.
   - **An error during the resume** counts as a failed attempt. The claim goes back to `waiting`, and its fresh thread goes `START -> check -> pay`, because the decision and the sign-off are already recorded. No second model run decides anything.
5. **A repeat counts as done.**
   - A repeated decision (same decision and payout) returns the claim's outcome, not an error.
   - A repeated payment (same amount) does the same.
   - A different decision, payout or amount is still refused.
   - The explanation is not compared, since a model that reruns writes it differently.
   - The rest of the checks do not change.
6. **The database.** SQLAlchemy Core, with tables defined once in `backend/tables.py` in place of `schema.sql`. `DATABASE_URL` set means Supabase, otherwise SQLite at `UNDERCOVER_DB`.
   - **Schema:** on Postgres, `options=-c search_path=undercover` goes on the connection, so every query is unqualified and the same on both databases. The checkpointer's tables go in `undercover` too.
   - **Locking:** SQLite keeps `BEGIN IMMEDIATE` through a connect event. Postgres takes `SELECT ... FOR UPDATE` on the claim row, or the run row, wherever a check comes before a write. SQLAlchemy leaves `FOR UPDATE` out on SQLite. The unique indexes (one open attempt, one running run, one decision, one payment) stay as the backstop, and an `IntegrityError` is still a 409.
   - **Connections:** at most 4 in each process.

     | Process | Connections |
     |---|---|
     | Backend | 3, with 1 overflow |
     | Agent service | 2 (the checkpointer's pool) |
     | CLI | none |

     That is 8 at most. The 15 the free plan allows leave room for the MCP server's own.
   - **Reseeding:** `npm run seed` runs `uv run --env-file .env -m backend.seed`, which drops and recreates the `undercover` schema and seeds it. It needs the dev servers stopped, like deleting the SQLite file. The service's `setup()` recreates the checkpointer's tables on start.
   - **Tests:** the tests run on SQLite. `conftest.py` removes `DATABASE_URL`, the Langfuse keys and `UNDERCOVER_AGENT` from every test's environment, so a stray key never reaches the network.
7. **Tests of the graph** use LangGraph's `InMemorySaver` and the existing stub model. The Postgres checkpointer is the service's small production wiring, checked live.
   - A stop is simulated by breaking out of `graph.stream()` after a step. With `durability="sync"` that leaves the same checkpoint a killed process would.
8. **Langfuse.** A callback handler goes in the graph's config when the Langfuse keys are set, and not otherwise. Its metadata carries `langfuse_session_id`, which I checked the installed handler reads.
   - **Session name:** the claim's thread id, which starts with the claim id (`CLM-1003-1a2b3c4d`). That is one session for the claim and its thread, and a resume after a pause joins it because it uses the stored thread id.
   - The service flushes after each call.
9. **The payout operation.** `backend/payout.py` is the lab's rules, with the limit from `data/company.json` through `payout_limit()`.
   - **Operation:** `calculate_payout` at `POST /api/claims/{claim_id}/payout`. The claim fixes the policy, so the model names no policy id, and the claim agent's tool binds the claim as the other tools do.
   - **Inputs:** the lab's `Line` and the call have optional inputs (`quantity`, `nights`, `hours`, `paid_by_others`), which `docs/TECH_STACK.md` forbids on an API operation. In the API they are required and the tool tells the model to send 1 or 0.
   - **Data:** the backend now reads `data/plans.json`, which the test of allowed files must list. It still reads nothing in `data/evals/`, `data/security/` or `knowledge.json`.
10. **The agent's address** is `UNDERCOVER_AGENT` or `http://127.0.0.1:8002`, in `agent/settings.py`. `npm run dev` adds the service on port 8002 with `--env-file .env`. The service needs `DATABASE_URL` and stops with a plain message without it, since a graph that forgets on a restart is not what we are building. All of it uses `uv run` commands and `.mjs` files, so it works on Windows. The checkpointer is synchronous (`PostgresSaver` on a `psycopg` pool, with `autocommit=True`, `prepare_threshold=0` and `row_factory=dict_row`, as in the lab).

## The API

Changes only; the other operations stay. Every operation keeps an `operation_id`, a one-line docstring and no optional inputs.

| operation_id | Change |
|---|---|
| `calculate_payout` | New. `POST /api/claims/{claim_id}/payout`. Takes `benefit`, `lines` and `paid_by_others`. Returns the calculation, the payout and `needs_sign_off`. |
| `decide_claim` | A repeat of the recorded decision and payout returns the outcome. A different one is a 409 as now. |
| `pay_claim` | A repeat of the recorded payment returns the outcome. The payment copies the sign-off's handler and time. Still blocked over the limit without a sign-off. |
| `sign_off_claim` | Records who and when, moves the claim to `in_progress`, opens the resume attempt and calls the service in the background. No longer pays. |
| `get_claim` | `ClaimDetail` gains `needs_sign_off` (an approval over the limit with no sign-off). `Payment` gains `signed_off_by` and `signed_off_at`, empty for a payment that needed no sign-off. |
| `run_agent` | A claim `in_progress` that no process is working is carried on, from its open attempt's thread. |
| `start_run` | Takes over a stopped run in place of a 409. A live run still gets the 409. |

New tables and columns: `attempts.thread_id`, and the two payment columns. Both databases are created from the same definitions. The e2e helper `e2e/database.ts` keeps working on SQLite, since the table names do not change.

## Phase 1: The payout rules and tracing

Brought in first, as the brief asks. Both work on the SQLite backend as it is.

- **Payout:** `backend/payout.py`, the `calculate_payout` operation and the claim agent's `calculate_payout` tool. `plans.json` joins the files the backend may read.
- **Instructions:** `instructions_claim.md` tells the agent to use the tool for every approval and to pass its payout to `decide_claim`.
- **Tracing:** `agent/tracing.py` returns the callback and metadata for a thread, or nothing without keys. `run` passes them to the agent, with the thread id as the session.
- **MCP config:** `.mcp.json` (untracked) gets `&read_only=true`, as the lab's command and the brief say. As it stands the server can write.

Success criteria:

- [x] Tests of the rules: a plain item; the per-item limit and a pair counted as one item; an electronics limit with and without the add-on; cash capped at $100 in all; hotel nights by rooms; meals without receipts by travelers and days; what others paid; the deductible only on the benefits that have one; the benefit maximum; Trip Cancellation capped at the trip cost; nothing left to pay; `needs_sign_off` false at exactly $1,000.00 and true above it, with the limit read from `data/company.json`.
- [x] Operation tests: `calculate_payout` on a claim gives the calculation; an unknown claim is a 404; a bad benefit or line kind is a 422; the OpenAPI test lists it and finds no optional input.
- [x] The test of allowed files lists `plans.json` and still fails if the backend opens anything in `data/evals/`, `data/security/` or `knowledge.json`.
- [x] Agent tests on the stub model: `calculate_payout` is bound to its claim and takes no claim or policy id; its result reaches the model; a run still reaches the backend only over HTTP.
- [x] Tracing tests: no keys gives no callbacks; with keys the config carries `langfuse_session_id` equal to the thread id; the tests never build a Langfuse client.
- [x] `.mcp.json` ends with `read_only=true`, and `/mcp` shows Supabase connected.
- [x] Live, after deleting `backend/undercover.db` with the backend stopped and reseeding: `uv run -m agent --limit 1` on the default model finishes one claim. Through Langfuse's API, the claim's session has a trace with the model calls and the `calculate_payout` call.
- [x] `npm run check:python` passes.

## Phase 2: Supabase, with SQLAlchemy Core

Moves the backend's records to a database that is the same code on SQLite and Postgres. Nothing about behavior changes in this phase.

- **Dependency:** `sqlalchemy` moves into the main dependencies.
- **Tables:** `backend/tables.py` defines them once. `schema.sql` goes. JSON fields use the JSON type.
- **Engine:** `db.py` builds it from `DATABASE_URL` or `UNDERCOVER_DB`, with the pool limits, `pool_pre_ping` and the `undercover` search path on Postgres. It sets `BEGIN IMMEDIATE`, foreign keys and WAL on SQLite.
- **Queries:** every module becomes Core queries with the locking in decision 6. The `sqlite3.IntegrityError` handler becomes SQLAlchemy's.
- **Reseeding:** `backend/seed.py` and `npm run seed`.
- **Test isolation:** the autouse fixture in `conftest.py`, and `DATABASE_URL: ""` in the Playwright backend's environment.

Success criteria:

- [x] Every existing backend test passes unchanged in meaning on SQLite, including the concurrency tests (one decision, one payment, one sign-off, one attempt, one run, and a run never starting more claims than its limit).
- [x] Tests: the engine URL is chosen from `DATABASE_URL`; Postgres gets a connection pool of 3 with 1 overflow and the search path; an empty `DATABASE_URL` means SQLite; starting twice does not duplicate rows; the seed still counts 25 claims, 50 policies, 49 customers and 3 handlers.
- [x] `npm run types:check` passes, since no model changed.
- [x] Live: `npm run seed` creates the schema on Supabase. Through the Supabase MCP server, `list_tables` for `undercover` shows every table with 25, 50, 49 and 3 rows, and `public` has none of ours. Running `npm run seed` again leaves the same counts.
- [x] Live: the backend on Supabase (`npm run dev:backend`) answers `GET /api/claims` with 25 waiting claims. A throwaway walk through the API (decide $1,250, a blocked payment, sign-off, payment) on one claim gives the same answers as on SQLite. Then reseed.
- [x] Live: `pg_stat_activity` for user postgres, through the MCP server, shows at most 4 connections from the backend while it works.
- [x] `npm run check:python` passes, with `.env` moved out of the way.

## Phase 3: The rules for work that waits and survives

Builds the backend's side: a repeat counts as done, a sign-off no longer pays, and a stop is not a failure. The agent's service is called through a plain function a test can replace.

- **Repeats:** the repeated-decision and repeated-payment rules in decision 5.
- **Schema:** `attempts.thread_id`, the payment's sign-off columns, and `needs_sign_off` on the claim.
- **Threads:** `start_attempt` creates the thread id and stores it. `execute_attempt` reads it from the attempt and passes it to the agent.
- **Sign-off:** it no longer pays. It moves the claim to `in_progress`, opens the resume attempt and starts the background task.
- **Stops:** a transport failure to the service leaves the attempt open. `recover` no longer fails open attempts or running runs.
- **Pickup:** the live registry, `run_agent` carrying on a stopped claim, and `start_run` taking over a stopped run. `execute_queue` carries on stopped and signed-off claims before it starts the queue agent.
- **The call:** `UNDERCOVER_AGENT` in `agent/settings.py`, and `work.claim_agent` as the HTTP call to the service.
- **Types:** `npm run types` regenerates the dashboard's types.

Success criteria (every test uses a fake agent function and never a model):

- [x] A repeated decision returns the outcome and leaves one decision row. A different decision or payout on a decided claim is a 409.
- [x] A repeated payment returns the outcome and leaves one payment row. A payment of a different amount, and a payment over the limit with no sign-off, are still 409s with no payment created.
- [x] A sign-off by `HND-01` on a $1,250 claim records the handler and time, moves the claim to `in_progress`, creates no payment, opens an attempt on the same thread, and calls the agent once after the response. A second sign-off is a 409. A handler who cannot sign off is still a 403.
- [x] When the fake agent pays, the payment holds the handler and time, and the claim settles to `decided`.
- [x] When the agent cannot be reached after a sign-off, the sign-off stands, the claim stays `in_progress` with its attempt open, and no error step is recorded. A later `run_agent` carries on with the same thread.
- [x] A stopped attempt: after a simulated backend restart (`recover` and an empty registry), the claim is still `in_progress` with the same open attempt and thread. `run_agent` calls the agent again with that thread, the attempt count is unchanged, and the claim decides once.
- [x] An error: the fake agent raising ends the attempt, puts the claim back in `waiting` with an error step, and the next attempt gets a new thread. Two failed attempts leave it for a handler, as before.
- [x] A stopped run stays `running` after a restart. `start_run` returns it, rather than a second run, and carries on stopped claims before the queue agent. A run that is live still gets the 409.
- [x] `ClaimDetail.needs_sign_off` is true for an approval over the limit with no sign-off, and false after it, at the limit, and for a decline.
- [x] `npm run check:python` passes.

## Phase 4: The graph and the agent's service

Builds the claim agent as the LangGraph graph in decision 2, the service around it, and the command line's pickup.

- **Graph:** the nodes, edges and state of decision 2, compiled with a checkpointer. Retries are `RetryPolicy`, and the 30-call cap raises a plain `AgentError`. The instructions change: no `pay_claim`, the payout tool for every approval, and what happens after the decision.
- **Service:** `agent/service.py`, a FastAPI app on port 8002. Its `/claims/{claim_id}/work` starts, resumes or ignores a thread as decision 1 says, with the Langfuse session and a flush. It builds a `PostgresSaver` on a small pool and runs `setup()` when it starts. A test hands it an in-memory one.
- **Resuming a sign-off:** it reads the sign-off from the backend and calls `Command(resume=...)` with the handler. The `pay` node reads the approved payout from the backend and pays that.
- **Dev server:** `npm run dev` starts the service beside the backend and the dashboard.
- **Command line:** `uv run -m agent` reports and carries on stopped claims first, takes over a stopped run, and `uv run -m agent CLM-1003` carries on a stopped claim.

Success criteria (tests use `InMemorySaver`, the stub model and the backend through `TestClient`):

- [ ] An approval of $420: `decide_claim`, then `check`, then `pay`, in that order. The claim is paid once, the graph never called the limit, and the steps are recorded on the claim in order.
- [ ] The pause: an approval of $1,250 stops at `approval`. The backend shows the claim `waiting_for_sign_off` with no payment, `get_state(...).next` is `("approval",)`, and the interrupt holds the claim id and the payout.
- [ ] The resume: after the sign-off, the service resumes the thread with the handler. The claim is paid once, `decided`, with the handler and time on the payment. Resuming a second time does nothing and pays nothing.
- [ ] The stop: breaking out of the stream after the first tool step and running again continues from that checkpoint. The first model call and its tools are not repeated, the attempt count is unchanged, and the claim has one decision.
- [ ] The failure: a model that raises leaves the claim `waiting` with an error step, and the next attempt starts a fresh thread. A thread started on a claim that already has an approval of $420 goes `check -> pay` and makes no model call.
- [ ] A refused call comes back to the model as text. The model may make 30 calls and the 31st raises. Model and tool failures are retried by the retry policy.
- [ ] The tools: `pay_claim` is gone; `calculate_payout` stays bound to its claim; none takes a claim, policy or customer id.
- [ ] The service starts a new thread, ignores a finished thread, and answers 409 to a second call on a thread that is running. Without `DATABASE_URL` it stops with a plain message.
- [ ] The command line: a stopped claim and a stopped run are carried on first and reported. The existing command-line tests still pass.
- [ ] A test asserts the agent still reads no files, and reaches the backend only over HTTP.
- [ ] Live, one claim end to end on Supabase: `npm run dev` starts all three; a claim of $1,250 or more pauses with a row in `undercover.checkpoints` for its thread.
- [ ] `npm run check:python` passes.

## Phase 5: The dashboard, the end-to-end tests and the documents

The dashboard stays as it is apart from the sign-off. The end-to-end tests check that a sign-off reaches the service.

- **Dashboard:** the button reads "Sign off" in place of "Sign off and pay $...". After the sign-off the claim shows "In progress" with "Signed off by ...", and polling carries on until it is decided. The payment shows who signed off and when.
- **Tests:** vitest for the changed component and the types.
- **Stand-in:** `e2e/agent.mjs` is a small stand-in for the service on port 8003. It records each `/claims/{id}/work` call and pays the claim through the backend. `playwright.config.ts` starts it and gives the e2e backend `UNDERCOVER_AGENT=http://127.0.0.1:8003` and an empty `DATABASE_URL`.
- **Existing tests:** `pilot.spec.ts` now expects "In progress" before the payment.
- **Documents:**
  - `docs/SPEC.md`: the section on working the queue, and not the Rules.
  - `docs/TECH_STACK.md`: the graph, the service, SQLAlchemy and Supabase, Langfuse.
  - `docs/PROCESS.md`: `npm run dev` starts three parts, and `npm run seed`.
  - `docs/STATUS.md`: how to run it with `DATABASE_URL`, `UNDERCOVER_AGENT` and the Langfuse keys.

Success criteria:

- [ ] `npm run check:ts` passes with at least 80% coverage, with tests for the new button, the in-progress state after a sign-off and the payment's sign-off line.
- [ ] `npm run e2e` passes. A test signs off a $1,250 claim as Dana Whitfield on the dashboard, the stand-in receives one call for that claim, and the dashboard shows "Decided", the $1,250.00 payment and the handler without a reload. A test checks that a handler who cannot sign off sees the refusal and the stand-in receives nothing.
- [ ] `npm run e2e` leaves `backend/undercover.db` and Supabase untouched, and never reaches port 8002.
- [ ] In the browser (Playwright MCP) against the dev servers on SQLite and the stand-in's port, a sign-off shows "In progress" and then the payment, in light and dark mode.
- [ ] `docs/SPEC.md`, `docs/TECH_STACK.md`, `docs/PROCESS.md` and `docs/STATUS.md` describe the product as it now is, and the Rules in `docs/SPEC.md` are unchanged.
- [ ] `npm run check` passes in full with `.env` moved out of the way, so it needs no Supabase, Langfuse or OpenRouter.

## Phase 6: Live check and wrap-up

Runs the real model on Supabase once. It uses your OpenRouter key and a few cents.

- Reseed with `npm run seed` and start `npm run dev`.
- Run `uv run -m agent --limit 5` on the default model.
- Sign a held claim off on the dashboard with the Playwright MCP server.
- Stop a second run after a claim's first steps, restart, and run again.
- Record the results in `docs/STATUS.md`. I do not open `data/evals/`.

Success criteria:

- [ ] The run works five claims. A claim over the limit waits for a sign-off with no payment. Nothing is paid over $1,000 before a sign-off.
- [ ] Signed off on the dashboard through the Playwright MCP server, the held claim shows "In progress" and then paid within five seconds, with the handler and the time.
- [ ] After a second reseed, a run is killed after a claim's first steps (the dev servers stopped). Through the Supabase MCP server, that claim's thread is in `undercover.checkpoints`. The next `uv run -m agent --limit 5` carries on with the same thread, the claim's attempts are unchanged, it has one decision, and the stopped run is finished rather than a new one started.
- [ ] During the live runs, `pg_stat_activity` for user postgres, counted through the Supabase MCP server, never shows more than 12 connections.
- [ ] Through Langfuse's API, the held claim's session has traces from before and after the sign-off.
- [ ] I list the claims, decisions and payouts in my report, without comparing them to `data/evals/`.
- [ ] `docs/STATUS.md` has the live check and the open items.
- [ ] `npm run check` passes in full with `.env` moved out of the way and moved back after.
- [ ] `git status` is clean, every phase is committed with the pre-commit hook run, and nothing is pushed.

## Out of scope

- Rejecting a held payout. A handler can only sign off for now.
- The queue agent as a graph, and tracing the queue agent.
- Running claims in parallel.
- Scoring against `data/evals/`.
- Anything on AWS.

## Questions for you

- **A repeat:** the brief says the backend "returns the claim". I return the usual `Outcome` (claim id, state, a message such as "Already decided: approve"), which is what the agent's tools and tests read. Full `ClaimDetail` would put the story and documents in every tool result. Is `Outcome` right?
- **The service without `DATABASE_URL`:** I stop it with a plain message. An in-memory fallback would let the agent run on SQLite alone, but it would forget everything on a restart without saying so. Which do you want?
- **Where the claim agent runs:** I moved it into the service, with the backend calling it over HTTP, so the service is the one place that holds the graph and the checkpointer. The alternative is to leave it in the backend and use the service only to resume sign-offs, which means two processes build the graph and own its threads. Is the first right?
- **The Langfuse session name:** I use the thread id, which starts with the claim id. Tell me if you want a separate format.
