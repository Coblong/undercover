# Plan: code quality

Follows from `docs/REVIEW.md` (finding ids such as A1 and C1 refer to it). UnderCover is an educational project, so this plan is about well-written code and good habits as features pile up, not about launching. It follows `docs/CODE_STANDARDS.md` and `docs/PROCESS.md`, and changes nothing the spec asks for.

## What this plan does and does not do

It does the findings the review marks "Fix now", and some of "Fix if it is cheap". It leaves alone:

- What the Week 2 and 3 briefs build: Postgres, the payout calculator, Langfuse, the agent's own service, the dispatcher, versioned instructions, sign-in, the agent's API key and the AWS builds. In particular I do not tidy `agent/queue.py` or `backend/runs.py`, since the dispatcher replaces them, and I keep the database code plain `sqlite3`, since DURABLE rewrites it.
- Production-only work: money as cents, migrations, an audit table, request ids, readiness checks, CI, splitting the dependencies, and provider data policy. Reseeding by deleting the database stays the way to change the schema.

Nothing in the product's behavior changes except where a phase says so: a decision with a nonsense payout is now refused, and the customer history now includes claims decided here.

## Design decisions

Please review these first.

1. **The backend validates decisions.** An approval's payout is above 0 and at most the amount claimed. Numbers are finite. The explanation and policy section are non-empty and bounded, and the policy section is a section that exists in the wording or the manual. A refusal is a 422 whose text says what to fix, so the model can act on it, as with the existing refusals.
2. **Writes that read first run in one transaction.** A small helper opens it with `BEGIN IMMEDIATE`, so two requests cannot both pass a check. Unique indexes are the backstop, and a lost race is a 409 with the same text a second, sequential call gets, never a 500.
3. **The claim agent's tools are built for one claim.** Like `queue_tools(run_id)`, a `claim_tools(claim_id)` factory fixes the claim in each tool, so the model has no id to change. This is enforced in the agent, not the backend. Making the backend enforce it is the job of SECURE's API key.
4. **The customer history is built from the data we hold.** The seeded earlier claims, plus every claim in this system for that customer filed before the claim in hand, with its outcome. The history becomes claim-scoped: `GET /api/claims/{claim_id}/claim-history`.
5. **Settings are read once**, in one small module per process, with a clear error for a missing key. The environment beats `.env`.
6. **The backend's HTTP layer stops running agents.** Starting and ending work moves out of `backend/main.py`, and the agents become a dependency the tests replace with FastAPI's dependency overrides, in place of patching module globals.

## Phase 1: A backend that refuses bad input and survives races

Findings A1, A4, A5, C1, C3, F3.

- The validation in decision 1. A step's text, a summary and a to-do list get length limits.
- The transaction helper in decision 2, used by `decide`, `pay`, `sign_off`, `start_attempt`, `end_attempt` and `runs.create`. `end_attempt` becomes a single transaction.
- Schema hygiene: foreign keys switched on, WAL mode, indexes on `steps(claim_id)`, `attempts(claim_id)` and `attempts(run_id)`, a unique index so a claim has at most one open attempt, and one so only one run is `running`. These apply to a new database, so reseed.
- The claim-scoped history in decision 4, used by the dashboard and the agent. The old customer-scoped route is removed.

Success criteria:

- [x] Tests cover every refusal: `NaN`, `Infinity`, negative, zero and above-claimed approval payouts; empty and over-long text; a section that does not exist. Each gives a 422 and leaves the claim unchanged.
- [x] A test shows every decision the stub claim agents in `agent/tests` make is still accepted.
- [x] A concurrency test runs 8 threads on `pay`, `decide`, `sign_off`, `start_attempt` and `start_run`, 50 times each, and always finds exactly one success, the rest 409, and no 500.
- [x] A fault-injection test makes the second write inside `end_attempt` fail. The claim is unchanged, still `in_progress`, and recovery at the next start settles it.
- [x] A test shows the database itself refuses a step for a missing claim, a second open attempt on a claim and a second running run, and a connection has foreign keys, WAL and a busy timeout set.
- [x] A test shows the second claim of `CUS-3005` lists the first as open, that deciding it changes that history to its outcome and amount paid, and that a claim filed later never appears in an earlier claim's history.
- [x] `docs/STATUS.md` says to reseed after this change. `npm run check:python` passes with at least 80% coverage.

## Phase 2: The agent's boundaries, settings and logs

Findings B2, B5, C5, D6, E1 (minimal), A6.

- `claim_tools(claim_id)` as in decision 3. The tools lose their id arguments, and the instructions stop telling the model to "use the `policy_id` shown on the claim".
- A settings module per process, as in decision 5: the backend address, the model key and the model name. `load_dotenv()` no longer overrides the environment and runs in the entry points, not on import. The agent's model is built once per run. The dev and e2e setups pass the backend's address explicitly, so an agent can never write to a different backend by accident.
- `call()` copes with an error body that is not JSON. A step that could not be posted is logged, not ignored.
- Logging from the standard library. A background failure is logged with its traceback, and each ended attempt logs one line: claim, run, the state it ended in, whether it failed, how long it took. The step the dashboard shows for a crash shrinks to the exception's class and a short message.
- The instructions take the approval limit, the filing window and the repeat-claim count from `data/company.json` when they are served, so the prompt and the data have one source.

Success criteria:

- [x] A test shows the tools have no argument the model can use to name another claim or customer, and that each calls only its own claim's routes.
- [x] A test starts the agent with no `OPENROUTER_API_KEY` and gets one clear error naming the variable. A real environment variable beats `.env`, and importing `agent.main` reads no file.
- [x] A test shows the agent's client uses the configured backend address, and the e2e and dev scripts set it.
- [x] A test puts a 500 with a plain-text body and a connection error through `call()` and gets text back in both cases.
- [x] A test with `caplog` shows a crashed attempt logs its traceback and an attempt line with its fields, and the step shown has no file path or URL in it.
- [x] A test shows the instructions change when `data/company.json` changes and hold no hard-coded `$1,000`, 90 or 12.
- [x] A live check on five claims on the default model (reseed first, cap the run at five) finishes with no call refused because of this phase. Report the claims and decisions, without comparing them to `data/evals/`.
- [x] `npm run check:python` passes.

## Phase 3: A cleaner structure

Findings from section I of the review, and part of H1.

- `backend/main.py` keeps the routes only. `execute_attempt`, `execute_queue` and the code that starts work move to `backend/work.py`. The claim agent and the queue agent reach it through a FastAPI dependency, which the tests override.
- `backend/rules.py` splits by subject: decisions, payments and sign-off in one module, attempts and recovery in another. `result_line`, which is wording for the queue agent, moves out of the rules.
- `unfinished_reason` returns a small enum and a separate table turns it into text, so control flow no longer depends on strings.
- `work_claim` returns its own result model, in place of reusing `Outcome`.
- Step kinds, states and to-do statuses are defined once on the Python side, and the TypeScript maps in `format.ts` are keyed on the types so a missing label is a compile error. (The generated types in Phase 4 make that automatic.)

Success criteria:

- [x] The backend tests and the agent tests pass with no edits to what they assert, only to how they replace the agent. No test patches an attribute of `backend.main` any more (a `grep` for `backend_main` finds none, and the remaining patches of `main` are of `agent.main`).
- [x] `backend/main.py` is under 150 lines and contains no agent or run logic; no module in `backend/` is over 200 lines.
- [x] A test fails if a route is added with no `operation_id`, as today, and the OpenAPI document is unchanged except for the history route and `work_claim`'s result.
- [x] `docs/STATUS.md` describes the modules. `npm run check:python` passes.

## Phase 4: The dashboard and the tests

Findings G1 to G4, H1, H2.

- Errors show the backend's reason. A refused sign-off or run says why, in the API's own words.
- Polling pauses while the tab is hidden and resumes when it returns.
- List keys use ids, not text (the to-do list, the items table).
- The dashboard's types are generated from the backend's `openapi.json` with `openapi-typescript`, a new development dependency, so a change to a model fails the check until the types are regenerated. `uv run -m backend.openapi` writes the document and `npm run types` writes the types.
- A test that runs the agent against a backend that returns errors and timeouts partway through a claim (an `httpx.MockTransport`), and checks the claim ends `waiting` or left for a handler, never stuck.

Success criteria:

- [ ] Vitest tests show a 409 reason reaches the screen for sign-off and for starting a run, polling stops while the document is hidden and starts again, and a repeated to-do text and a repeated item description render with no key warning.
- [ ] Changing a field in `backend/models.py` without regenerating makes `npm run check` fail, and regenerating makes it pass.
- [ ] A test with a backend that fails on every call after the third leaves the claim `waiting` first and left for a handler after a second try, with no claim `in_progress`.
- [ ] In the browser (Playwright MCP), a refused sign-off shows its reason, in light and dark mode.
- [ ] `npm run check` passes in full, with at least 80% coverage on both sides.
- [ ] `git status` is clean, every phase is committed with the pre-commit hook run, and nothing is pushed.

## Questions for you

- Generated TypeScript types (Phase 4) add one development dependency, `openapi-typescript`, and a small build step. Worth it, or keep the types by hand?
- Do you want the optional end-to-end test where Playwright drives the real agent against a stub model server? It teaches the most and is the largest piece, so I left it out. It would add a configurable model address and a small server in `e2e/`.
- Phase 3 is a refactor with no new behavior. If you would rather keep adding features first, Phases 1 and 2 are the ones to do before the Week 2 builds; Phases 3 and 4 can wait.
