# Plan: from pilot to production

Follows from `docs/REVIEW.md`, which lists every finding (A1, B2 and so on) and says which ones a later brief already covers. This plan does the rest. It follows `docs/CODE_STANDARDS.md` and `docs/PROCESS.md`, and changes no behavior the spec asks for.

## What this plan is, and is not

Production-like means that a real claim, a real customer's data and real money could pass through the system without a known way to lose, duplicate or leak any of them, and that when something goes wrong someone can see what and why.

**This plan does not make the system safe to expose.** Until sign-in exists (B1), the API lets anyone sign off a payout as any handler. That is scheduled for IMPROVE and SECURE, and I will not build it twice. Until then the system stays on a trusted network, with no real customer data (see Release gate, below).

**Left to the later briefs, on purpose:**

| Finding | Brief |
|---|---|
| Postgres, SQLAlchemy, Supabase (C4) | DURABLE |
| The payout calculator in the backend (A3) | DURABLE |
| Langfuse tracing (D2) | DURABLE |
| The agent in its own restart-safe service, and a replay of a decision or payment treated as done (C2, C7, C8) | DURABLE |
| A dispatcher in place of the queue agent (C6), and token, cost and time records (E2, D1) | SWARM |
| Versioned instructions (D4), and sign-in (B1) | IMPROVE |
| The agent's API key, the hidden-instruction check, the Lambda build (B1, H3, H4 in part) | SECURE |
| The complete decision record (A8) | CONTEXT |
| Sending a claim back to the agent (A7 in part) | RUNTIME |

I put the phases in the order that is cheapest to do now. Phases 1 and 2 change the schema and the money type, so do them before DURABLE moves the database, or the work is done twice. Phases 3 to 6 do not depend on that.

## Design decisions

Please review these first.

1. **Migrations are a small runner I write**: numbered `.sql` files in `backend/migrations/`, applied in order, recorded in a `schema_version` table. No new dependency. DURABLE moves to SQLAlchemy, and the numbered files port across. The alternative is Alembic with SQLAlchemy, which is better tooling but a larger change now.
2. **Money is whole cents in the database and in the code, and dollars as JSON numbers on the wire.** The API keeps its shape, so the agent's tools and the dashboard do not change. One module converts at the edge, and rejects a value with more than two decimals or that is not finite. The alternative, strings on the wire, is more correct and breaks every consumer.
3. **A decision is validated by the backend, not trusted.** An approval's payout is above 0 and at most the amount claimed. Both texts are non-empty and bounded, and the policy section must be a section that exists in the wording or the manual. A refusal is a 422 whose text the model can act on, as the existing refusals are.
4. **Concurrency is settled by the database.** Each read-then-write runs in one transaction that takes the write lock first. Unique indexes are the backstop. A lost race is a 409, never a 500.
5. **The agent is confined to its attempt, not trusted.** Starting an attempt issues a random token. Every operation the claim agent uses must carry it, and the backend refuses it for any other claim. SECURE's API key will say who the caller is; this says which claim it may touch. They layer.
6. **The dashboard's types are generated from the backend's `openapi.json`**, so a change to a model fails the check until the types are regenerated.
7. **Nothing seeds a production database.** Seeding is an explicit command. The server only migrates.
8. **Logs are JSON lines from the standard library**, with a request id. No logging dependency.

## Phase 1: A schema that can change

Findings F1, F2, F3 and part of C4. Done first because every later phase changes the schema.

- The migration runner, and a first migration that is today's schema exactly. A database made before migrations (the dev database) is recognized and marked as at version 1.
- A second migration adds the constraints and indexes: foreign keys from decisions, payments, sign-offs, steps, attempts and to-dos to their claims and runs; indexes on `steps(claim_id)`, `attempts(claim_id)`, `attempts(run_id)`; a unique index so only one run can be `running`; a unique index so a claim has at most one open attempt.
- `connect()` turns on foreign keys, WAL mode and a busy timeout.
- Seeding becomes `uv run -m backend.seed`. The server's start-up migrates and recovers but never seeds. `npm run dev`, `npm run e2e` and the test fixture call the seed command, so the course workflow is the same.

Success criteria:

- [ ] A test builds a database in the pilot's original schema with some claims, steps and a decision, runs the migrations, and finds every row intact and the new constraints in place.
- [ ] A test starts the server on an empty migrated database and finds an empty queue, then runs the seed command and finds 25 claims; running the seed twice leaves 25.
- [ ] A test shows a foreign key violation is refused (a step for a claim that does not exist) and a second open attempt on one claim is refused by the database.
- [ ] A test shows a second running run is refused by the database even when the application check is bypassed.
- [ ] `PRAGMA foreign_keys`, `journal_mode` and the busy timeout are checked on a new connection.
- [ ] `npm run dev` on a missing database creates and seeds it; the dev database from the harness build opens and works.
- [ ] `docs/STATUS.md` says how to migrate, seed and reset. `npm run check:python` passes.

## Phase 2: Money and decision integrity

Findings A1, A2, A5, C1 and C3.

- Cents everywhere, as in design decision 2. A migration converts `REAL` columns to integer cents.
- `decide` validates as in decision 3. The policy-section check reuses the parsed sections in `documents.py`. `decide_claim`'s tool description and the claim instructions say the section is written as "3. Trip Delay".
- Each write operation runs in a transaction that takes the write lock first (`BEGIN IMMEDIATE`). `end_attempt` becomes one transaction. A lost race returns a 409 with the same text a sequential second call gets.
- Bounded text: an explanation up to 2,000 characters, a section up to 100.

Success criteria:

- [ ] Tests cover every refusal: `NaN`, `Infinity`, negative, zero and above-claimed approval payouts; a payout with three decimals; empty and over-long text; a section that does not exist. Each gives a 422 and leaves the claim unchanged.
- [ ] A test shows the live path is not broken: every decision the stub claim agents in `agent/tests` make is accepted.
- [ ] A test pays $420.10 and $0.10 and reads them back exactly, and a sum of ten payments of $0.10 equals $1.00 exactly.
- [ ] A concurrency test runs 8 threads on `pay`, `decide`, `sign_off`, `start_attempt` and `start_run` for one claim or run, 50 times each, and always finds exactly one success, the rest 409, and no 500.
- [ ] A fault-injection test makes the second write inside `end_attempt` fail; the claim is unchanged (still `in_progress`), and recovery at the next start settles it.
- [ ] The API's responses are unchanged: the existing dashboard tests and e2e tests pass without edits.
- [ ] `npm run check:python` passes with at least 80% coverage.

## Phase 3: Trust boundaries and configuration

Findings B2, B4, B5, B6 (the refusal part), C5 and D6, and the part of B3 that code can do.

- **Settings in one place.** Each process reads its environment once at start-up into a small settings object and fails with a clear message if something it needs is missing (the model key, for the agent). `load_dotenv()` no longer overrides the real environment, and runs in the entry points, not at import. The agent's backend address and the model's base URL are settings. The dev, e2e and test setups set them explicitly.
- **Attempt tokens**, as in decision 5. The operations the claim agent uses (`get_claim`, `get_policy`, `get_claim_history`, the two document reads, `decide_claim`, `pay_claim`, `add_step`) take the token in a header. The read of a policy and a claim history become claim-scoped (`/api/claims/{claim_id}/policy` and `/claim-history`), so the agent can no longer ask for another customer's data. The dashboard keeps reading with no token; handler operations are untouched until sign-in exists.
- **Limits.** A request body limit, a maximum length on every free-text field (steps 20,000, summaries 4,000) and on the to-do list (100 items), an explicit CORS policy (same origin only), and `/docs` and `/openapi.json` switched off by a setting.
- **`call()`** handles an error body that is not JSON, and a failed step post is logged, not ignored.
- **Provider data handling.** Check OpenRouter's options for refusing providers that keep or train on data, apply them in the model request, and write the decision in `docs/TECH_STACK.md`.

Success criteria:

- [ ] A test starts the agent with no `OPENROUTER_API_KEY` and gets one clear error naming the variable; a real environment variable beats `.env`; importing `agent.main` reads no file.
- [ ] A test for the address: the e2e and dev setups pass the backend's own address to the agent, and the agent's client uses it.
- [ ] Tests show an attempt token for `CLM-1001` is refused with a 403 for `decide_claim`, `pay_claim`, `add_step`, `get_claim` and the document reads on `CLM-1002`, a request with no token is refused with a 401, and a token is dead once its attempt ends.
- [ ] A test shows the agent's tools no longer have a `claim_id` or `customer_id` argument the model can change, and that `get_claim_history` returns only that claim's customer.
- [ ] Tests show a 300 KB body, a 20,001 character step and a 101 item to-do list are refused, a cross-origin request gets no CORS grant, and `/docs` is 404 with the setting off.
- [ ] A test puts a 500 with a plain-text body and a connection error through `call()` and gets text back in both cases.
- [ ] A live check on five claims on the default model finishes with no refused call that the instructions caused. Reseed first and cap the run at five. Report the claims and decisions without comparing them to `data/evals/`.
- [ ] `docs/TECH_STACK.md` records the provider decision. `npm run check:python` passes.

## Phase 4: Records the business relies on

Findings A4, A6 (the thresholds), A7 and the referral part of A1.

- **Live customer history.** `get_claim_history` is claim-scoped (Phase 3) and returns the seeded earlier claims plus every claim in this system for that customer that was filed earlier, with its outcome and amount paid from the decision and payment, or "open" if undecided. Order is by filing date.
- **The instructions take their numbers from `data/company.json`**: the approval limit, the filing window and the repeat-claim count are filled in when the instructions are served. The prose stays in `backend/instructions_claim.md`. `GET /api/instructions/{agent}` still returns plain text.
- **Referrals have a destination.** `decide_claim` gains a required `refer_to` (`none`, `handler`, `special_investigations`). A referral must name one, anything else must say `none`. The claim instructions say which referral the manual calls for.
- **A worklist for handlers.** A new read operation, `list_worklist`, returns the claims that need a person, oldest first, each with a reason: a payout waiting for sign-off, a referral (with its destination), or a claim left for a handler. The dashboard gets a "Needs a handler" view. A handler can return a left claim to the queue, picked the way sign-off picks a handler today.

Success criteria:

- [ ] A test shows `CLM-1002`'s history (or the second claim of `CUS-3005`) includes the other claim in the queue as open, and that deciding one claim changes the next claim's history to show the outcome and the amount paid.
- [ ] A test applies the manual's repeat-claim rule: a customer with two earlier claims in the last 12 months, one seeded and one decided here, shows both in the third claim's history, and a claim filed later never appears in an earlier claim's history.
- [ ] A test shows the instructions change when `data/company.json` changes, and carry no hard-coded `$1,000`, 90 or 12.
- [ ] Tests cover every `refer_to` rule, and the e2e and agent stubs are updated.
- [ ] Tests show the worklist returns the three kinds of claim in order, with the right reason, and returns a returned claim to the queue with its failed attempts cleared and a step that says who returned it.
- [ ] In the browser (Playwright MCP) the "Needs a handler" view shows a held payout, a referral and a left claim, in light and dark mode, with no horizontal scroll at 1280 px.
- [ ] `npm run check` passes.

## Phase 5: Operability

Findings E1, E3, B6 (the stored text), and part of E2.

- **Logging.** JSON lines from the standard library, with a request id taken from `X-Request-ID` or generated, returned in the response and added to every log line in the request. Background failures are logged with their traceback. The text stored in a step for a crash shrinks to the exception's class and a short message; the detail is in the log.
- **An audit log.** A table that records every write operation: when, the operation, the claim or run, who (the agent, by its attempt, or "unauthenticated" until sign-in exists), the request id and the outcome, including refusals. It is written by one piece of code that every write goes through, so a new operation cannot forget it.
- **Readiness.** A new `ready` operation that checks the database and the schema version and returns a 503 if either is wrong. `health` stays a plain liveness check.
- **A line for every attempt.** When an attempt ends, one log line records the claim, the run, the state it ended in, whether it failed, the duration, the model, and the tokens as the model response reports them. SWARM will store these; this makes them visible until then.
- **One error shape.** An unhandled exception returns `{"detail": ..., "request_id": ...}` with a 500, and logs the traceback.

Success criteria:

- [ ] A test calls every `POST` and `PUT` operation in `openapi.json` (found by reading it, so a new operation is included) and checks each leaves one audit row, refused calls included.
- [ ] A test shows a request id sent in is returned and appears in the log lines of that request, and one is generated when none is sent.
- [ ] A test with `caplog` shows a crashed attempt logs a traceback and an `attempt_ended` line with its fields, and that the step the dashboard shows has no file path or URL in it.
- [ ] A test shows `ready` returns 200 on a good database, and 503 on one with a missing table or an older schema.
- [ ] A test shows an unhandled error returns the shape above with the request id.
- [ ] A live run on five claims (reseeded, capped) leaves five `attempt_ended` lines with tokens and durations, and an audit row for each decision and payment. Report the tokens in total, which is the first cost figure we have.
- [ ] `docs/STATUS.md` describes the log format and the audit table. `npm run check:python` passes.

## Phase 6: Contracts, delivery and the dashboard

Findings G1 to G5, H1 to H5.

- **Generated types.** `uv run -m backend.openapi` writes `openapi.json`; `npm run types` generates `dashboard/src/api-types.ts` from it with `openapi-typescript` (a new dev dependency); `types.ts` re-exports from it, and `npm run check` fails if the generated file is stale.
- **Dashboard.** Errors show the backend's reason. Polling pauses while the tab is hidden and backs off after failures. Keys are stable (an id, not text). An error boundary wraps the app. Fonts are served from the app, not Google. `npm run build` works.
- **A full-path e2e with no real model.** The model's base URL is a setting (Phase 3). A small stub server in `e2e/` speaks the chat API and plays a script for named claims. Playwright starts it with the backend. Tests click "Run agent" and "Run the queue" and watch real decisions and payments come out of the real backend and agent code.
- **Dependencies in groups.** The product's runtime dependencies (about 7) go in the default `dependencies`; the course's tooling moves to a `course` group; `uv sync` still installs everything for students, through `default-groups`. `docs/TECH_STACK.md`'s one sentence about the single `pyproject.toml` is updated.
- **CI.** A GitHub Actions workflow that installs from the lock files, runs `npm run check`, the generated-types check, `npm run build`, and an audit of the Python and Node dependencies (`pip-audit` and `npm audit`, the first a new dev dependency), on every pull request.
- **A `.env.example`** with every setting, and a short deploy note in `docs/STATUS.md`.

Success criteria:

- [ ] Changing a field in `backend/models.py` without regenerating makes `npm run check` fail; regenerating makes it pass.
- [ ] Vitest tests show the 409 reason reaches the screen, polling stops while the document is hidden and resumes, a repeated to-do text and a repeated item description render without a key warning, and a thrown render error shows the boundary.
- [ ] `npm run e2e` passes with new tests where the dashboard starts the agent through the stub model server: "Run agent" on `CLM-1001` ends decided and paid $420.00; "Run the queue" with a limit of 2 ends with two decided and the rest waiting; a scripted model failure shows a problem step and a claim back in the queue.
- [ ] A fresh environment with only the default dependencies imports `backend.main` and `agent.main` and passes the Python tests; I report the number of installed packages before and after. `uv sync` with no options still installs everything, and a lab notebook's imports still work.
- [ ] `actionlint` passes on the workflow, and every command it runs passes locally. The workflow has not run on GitHub: you will see its first run on your first pull request.
- [ ] `npm run build` produces `dashboard/dist`.
- [ ] `npm run check` passes in full, with at least 80% coverage on both sides.
- [ ] `git status` is clean, every phase is committed with the pre-commit hook run, and nothing is pushed.

## Release gate

These must be true before a real customer's claim goes through, and this plan leaves them open:

- [ ] Handlers sign in and the backend checks who they are (IMPROVE 6). Until then, run on localhost only.
- [ ] The agent has its own credential (SECURE step 1), on top of Phase 3's attempt token.
- [ ] The check for instructions hidden in a claim is in place (SECURE step 2), since the claim agent reads text written by customers.
- [ ] The database is Postgres with backups and a tested restore (DURABLE).
- [ ] The business has decided what model providers may see and keep (Phase 3, and a decision that is not mine).
- [ ] The payout comes from the backend's calculator, not the model (DURABLE 1), and the agent has been scored against the handlers' decisions (SWARM).
- [ ] Someone owns the worklist from Phase 4, since nothing else will pick up a referral.

## Out of scope

Everything in the table at the top, plus: real payments and a ledger, messages to customers, pagination of lists (volume is 25 claims; add a `since` input when it matters), batching of step posts, and any change to what the agent decides.

## Questions for you

- Migrations as a runner I write (my pick), or Alembic with SQLAlchemy now?
- Money as cents inside and dollar numbers on the wire (my pick), or strings on the wire?
- Attempt tokens now (my pick, since they confine the agent), or wait for SECURE's API key and do both together?
- The worklist and "return to the queue" (Phase 4): now, or leave to RUNTIME and COPILOT?
- Moving the course's packages into a `course` group changes `pyproject.toml` and one sentence of `docs/TECH_STACK.md`. Fine, or leave the dependencies alone?
- Provider data handling is a business decision. Do you know what UnderCover would accept, or should I write the options for you to take to them?
