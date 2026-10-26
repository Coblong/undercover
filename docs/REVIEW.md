# Code review: the pilot and the harness build

A review of everything built so far: the pilot and the harness build. UnderCover is an educational project, so the question is whether the code is well written and follows best practices as it grows, not whether it is ready to launch. The first version of this review judged it against a production launch. Most findings stand as code quality findings, and the triage below says which are worth fixing in a course project. The plan that follows is `docs/PLAN_QUALITY.md`.

## Scope and method

- **Read in full:** `backend/` (7 modules, 1,150 lines), `agent/` (3 modules, 365 lines), `dashboard/src/` (13 modules and their tests), `e2e/`, the configuration (`pyproject.toml`, `package.json`, ESLint, lefthook, Playwright), and the seed data's shape.
- **Run:** `npm run check` (green), ruff with extra rule sets (ERA, BLE, TRY, ARG, RUF, complexity at 6), and two throwaway probes against a temporary database (results below under "Evidence").
- **Not done:** I did not open `data/evals/`, so nothing here says whether the agent's decisions are right. I did not load-test, and I did not review the course's labs or briefs, apart from skimming the later briefs to see what they already schedule.

## Verdict

The code is clean and well tested for its size. The rules that matter most live in the backend, not the prompt. The state machine is mostly sound, the checks are strict and green, and tests never call a model. The weaknesses are the ones that appear when a pilot grows:

- The backend trusts its inputs (payouts, texts) and does read-then-write without a transaction.
- The claim agent can name any claim or customer in its tool calls.
- Configuration is read in scattered places, and `.env` overrides the real environment.
- The HTTP layer in `backend/main.py` also runs the agents, and `rules.py` has become a catch-all.
- Nothing is logged.
- The customer history behind the repeat-claim rule is a frozen copy of the seed file.

None of these is hard to fix, and each is cheaper to fix before more features land on top.

## Triage for an educational project

| Verdict | Findings | Why |
|---|---|---|
| **Fix now** (correctness and good practice) | A1, A4, A5, A6, B2, B5, C1, C3, C5, D6, E1 (minimal), F3, G3, G4, I (the smells) | They are bugs or habits that later builds will copy. Each is small. |
| **Fix if it is cheap** | G1, G2, H1, H2 | Good practice, with a dependency or some effort. |
| **Leave to a later brief** | A3, A7 (part), A8, B1, C2, C4, C6, C7, C8, D1, D4, E2 | The briefs for Weeks 2 and 3 build them. Doing them now means doing them twice. |
| **Skip: production concerns only** | A2 (money as cents), B3, B4, B6, E3, F1, F2, F5, H3, H4, H5 | Not worth the churn in a course project. Money as `float` matches the course's own payout code. Reseeding by deleting the database is the course's workflow. |

**What to keep:**

- Rules in the backend. The agent can try to pay $1,250 and the backend refuses (`rules.py`, `pay`).
- `start_attempt` claims a claim with one conditional `UPDATE ... WHERE state = 'waiting'`, so two runs cannot start the same claim.
- Attempts, a bounded retry, and recovery at startup. A claim is never stuck.
- Every operation has an `operation_id`, a docstring and no optional inputs, and a test enforces it.
- A test proves the backend never opens `data/evals/`, and another that the agent reads no files.
- Strict tooling: ruff with 13 rule families, pyright, ESLint strict-type-checked with sonarjs and a11y, `tsc` strict, 80% coverage floors, and a pre-commit hook.
- Isolated e2e (own ports, own database).

## Findings

**Severity** below is as the first version judged it, against a production launch. The triage above is the one to follow for this project.

**"Scheduled"** means a later brief in `labs/` already covers the finding, so building it now would be building ahead, or building twice. "Not scheduled" means I found nothing in the briefs that covers it. The briefs I skimmed were DURABLE, SWARM, IMPROVE, SECURE, CONTEXT, RUNTIME and COPILOT.

### A. Money and the business rules

| ID | Sev | Finding | Scheduled? |
|---|---|---|---|
| A1 | High | `decide` accepts a payout that makes no business sense. See evidence 1 to 3. A payout 10 times the amount claimed is accepted, `Infinity` is accepted and sent for sign-off, `NaN` returns a 500, and an empty explanation and policy section are accepted. The manual (section 14) says a decision with no reason is "treated as not yet made". `rules.py:34-79`, `models.py` (`DecisionRequest`). | Not scheduled. The payout tool computes a payout; nothing validates one. |
| A2 | High | Money is a `float` throughout: the schema (`REAL`), the models, `round(x, 2)` and `!=` comparisons (`rules.py:48,94-98`). It works for two-decimal values today. It is the wrong type for a ledger, and it will be baked into the Postgres move. | Not scheduled |
| A3 | High | The payout is worked out by the model (known open item). The backend holds `plans.json` data it never uses, so it cannot check a payout against a plan maximum or a deductible. | Scheduled (DURABLE 1) |
| A4 | High | The customer history is a frozen copy of `customers.json` (`db.py:39`, `queries.py:87-95`). A decision made in this system never adds to it. The manual (section 9) counts "every claim, approved, declined or open" for the third-claim-in-12-months rule. `CUS-3005` already has two claims in the queue, and neither sees the other. | Not scheduled |
| A5 | Medium | `policy_section` is free text. A section that does not exist ("7. Dental") is stored as the reason for a decision. The backend already parses the sections of both documents (`documents.py`) and could check it. | Not scheduled |
| A6 | Medium | Thresholds are written into the instructions as prose ("90 days", "$1,000", "within 12 months") while `data/company.json` holds them as data. Two sources will drift. The manual (section 13) says to decide under the wording version in force when the policy was bought; only one version exists and nothing selects one. | Partly scheduled (IMPROVE versions the instructions) |
| A7 | Medium | A referral goes nowhere. The manual says a referral "says what is needed and who should look at it". Today a `refer` is a `decided` claim with free text. There is no worklist for referred claims, claims left for a handler, or sign-offs. A claim left for a handler after an approval of $1,000 or less cannot be paid at all, since `pay` refuses it (`rules.py:81-92`) and no handler action exists. | Partly scheduled (RUNTIME "sign off or send back") |
| A8 | Medium | The decision record lacks what the manual (section 14) requires: the calculation, who or what decided (agent, model, instructions version), and the manual and wording versions. | Scheduled (CONTEXT step 3, IMPROVE versions) |

### B. Security and access

| ID | Sev | Finding | Scheduled? |
|---|---|---|---|
| B1 | Critical | There is no authentication or authorization on any endpoint. Anyone who can reach the API can sign off a payout as any handler (evidence 4), write steps into any claim's audit trail, start runs, and read every claim's medical details. The spec's rule "the system must block the payment" holds only against the agent's own tool, not against the API. | Scheduled (IMPROVE 6, SECURE 1) |
| B2 | High | The claim agent is not confined to its claim. `decide_claim`, `pay_claim`, `get_claim` and `get_claim_history` take any id from the model (`agent/main.py:50-100`). The backend checks state, not ownership. A prompt injection in a story (the repo already has one, `data/security/`) or a plain model error can decide or pay another waiting claim of $1,000 or less, or read another customer's history, which the manual (section 11) forbids. | Not scheduled. SECURE gives the agent an API key but not a per-claim scope. |
| B3 | High | Customer stories, medical records and documents go to a third-party model provider through OpenRouter. Step text stores clipped tool results in plaintext SQLite. Nothing records which providers may see the data, retention, or a deletion path. | Not scheduled |
| B4 | Medium | No limits on input: a 5 MB step is accepted (evidence 8). There is no rate limiting. `/docs` and `/openapi.json` are open. There is no CORS policy, since the Vite proxy hides the need. | Not scheduled |
| B5 | Medium | `load_dotenv(override=True)` runs at import time (`agent/main.py:22`), so a `.env` file silently beats the real environment, and importing the agent from the backend loads the model key into the web process. `BACKEND_URL` defaults to port 8000 in the agent while the backend can run elsewhere (the e2e backend is on 8001). An agent started from a backend on the wrong port would write to a different backend. | Not scheduled |
| B6 | Low | Exception text goes straight into a step the dashboard shows (`main.py:150`, `"The agent stopped with an error: {exc}"`). It can carry internal URLs or provider messages. | Not scheduled |

### C. Concurrency and reliability

| ID | Sev | Finding | Scheduled? |
|---|---|---|---|
| C1 | High | Check-then-act without a transaction. `decide`, `pay`, `sign_off` and `runs.create` read, then write, in separate transactions. Only the primary keys stop a double payment. Eight concurrent payments gave one 200, six 409s and one 500 (evidence 6). The money is safe by luck of schema. The error is wrong, and the other rules (such as the sign-off check) have no such backstop. `rules.py:34-146`, `runs.py:39-49`. | Partly scheduled (DURABLE 6 "nothing happens twice" is about replays, not races) |
| C2 | High | The agent's work runs inside the web process. A run is a `BackgroundTask` (`main.py:155,174`). `work_claim` is a synchronous request that holds a server thread for minutes while the claim agent calls back into the same server (`main.py:194-203`). A restart or a `--reload` kills the run and charges an attempt to every claim in flight. Enough parallel work would starve the thread pool the agent itself needs. | Scheduled (DURABLE: the agent's own service, restart-safe work; SWARM: the dispatcher replaces the queue agent) |
| C3 | Medium | `end_attempt` is four separate writes (step, attempt, state, and for a left claim a second step) with no transaction (`rules.py:207-246`). If it fails part way (a locked database), the claim stays `in_progress` until the next restart. | Not scheduled |
| C4 | Medium | SQLite: no WAL mode, a 10 s busy timeout, a connection per request, `check_same_thread=False` (`db.py:113-118`). Parallel claim agents will meet "database is locked". | Scheduled (DURABLE: Supabase) |
| C5 | Medium | `call()` assumes every error body is JSON with a `detail` (`agent/main.py:46`). A 500 raises `JSONDecodeError` (evidence 5). Tools survive this through retry middleware, but fetching the instructions, posting steps and the CLI do not. A failed step post is silently ignored. | Not scheduled |
| C6 | Medium | `run_queue` reads its limit and its state from `/api/runs/latest`, not from its own run (`queue.py:68-70,95`). A second run would be read as the first. | Superseded (SWARM replaces the queue agent) |
| C7 | Low | `ToolRetryMiddleware` also retries the write tools. If `decide_claim` succeeds but its response is lost, the retry gets a 409 and the model sees an error for a decision that was recorded. | Scheduled (DURABLE 6) |
| C8 | Low | `thread_id` is passed to the agent (`agent/main.py:166`) but there is no checkpointer, so it does nothing. | Scheduled (DURABLE) |

### D. The agent

| ID | Sev | Finding | Scheduled? |
|---|---|---|---|
| D1 | Medium | The claim agent read 10 to 14 sections per claim in the live check, nearly every section it was pointed to. There is no token, cost or latency record to say whether that matters. | Scheduled (SWARM 4 for cost) |
| D2 | Medium | Steps are clipped to 600 characters (`agent/main.py:27,173`), so the audit trail on a claim loses the evidence it was shown. | Partly scheduled (Langfuse in DURABLE 2) |
| D3 | Medium | Each step is its own blocking HTTP request inside the streaming loop. A slow backend slows the agent. | Not scheduled |
| D4 | Medium | The instructions are read from a file on every request and have no version, so a decision cannot be traced to the instructions that produced it. | Scheduled (IMPROVE 1) |
| D5 | Low | Parallel tool calls are possible, so the order of steps is not deterministic (a flaky test showed it). | Not scheduled |
| D6 | Low | `model()` builds a client for every call, and a missing `OPENROUTER_API_KEY` is a `KeyError` at the first use, not at startup (`agent/main.py:114-122`). | Not scheduled |

### E. Observability and cost

| ID | Sev | Finding | Scheduled? |
|---|---|---|---|
| E1 | High | There is no logging at all in `backend/` or `agent/`. A grep finds only the CLI's prints. A background failure leaves a step row and nothing else. There are no request ids, no metrics, no alerts, and `/api/health` returns `ok` without touching the database. | Not scheduled (Langfuse covers model traces only) |
| E2 | High | No token, cost or latency is recorded for anything. The brief for this build said trials must be cheap, and nothing can say what a trial cost. | Scheduled (SWARM 4) |
| E3 | Medium | There is no audit log of who called which write operation. The steps table is the only trace, and anyone can write to it. | Not scheduled |

### F. Data and persistence

| ID | Sev | Finding | Scheduled? |
|---|---|---|---|
| F1 | High | There are no migrations. The schema is `CREATE TABLE IF NOT EXISTS`, so any column change means deleting the database. We told you to do that after the harness build. In production that is data loss. | Not scheduled |
| F2 | High | `init_db` reseeds when the claims table is empty (`db.py:128-135`). In production an emptied or misconfigured database would silently fill with invented claims, and a real claims table can legitimately be empty. | Not scheduled |
| F3 | Medium | No foreign keys, no `PRAGMA foreign_keys`, and no indexes besides primary keys. `steps` and `attempts` are filtered by `claim_id` and `run_id` in every request. | Not scheduled |
| F4 | Medium | Lists are unbounded: `list_claims`, steps, and the polling that re-sends them every 2 seconds. | Not scheduled |
| F5 | Low | Policies, items, documents and history are JSON text columns. Fine for now, but they cannot be queried. | Not scheduled |

### G. Dashboard

| ID | Sev | Finding | Scheduled? |
|---|---|---|---|
| G1 | Medium | `types.ts` is a hand copy of `models.py`. State names and step kinds are also repeated in `format.ts`. The backend already publishes `openapi.json` with `operation_id`s for exactly this; nothing generates the types from it. | Not scheduled |
| G2 | Medium | Four endpoints poll every 2 seconds whether or not the tab is visible, with no backoff and no conditional requests (`api.ts`). | Not scheduled |
| G3 | Medium | `postJson` throws a bare error (`api.ts:16-22`), so the screen says "Could not start the run." even when the backend said why ("A run is already going"). Sign-off failure is the same. | Not scheduled |
| G4 | Low | React keys use text that can repeat: `todo.text` (`RunPanel.tsx:69`) and `item.description` (`ClaimView.tsx:76`). | Not scheduled |
| G5 | Low | No error boundary, no production build or serving story (Vite's dev proxy only), the fonts load from Google's CDN, and there is no CSP. | Partly scheduled (SECURE step 4) |

### H. Tests, tooling and delivery

| ID | Sev | Finding | Scheduled? |
|---|---|---|---|
| H1 | Medium | Coverage is 99% and 98.8%, but there is no concurrency test, no migration test and no failure test against a slow or broken backend. The seams are module-global monkeypatches (`backend.main.run_claim_agent`, `RETRY_DELAY`, `CALL_LIMIT`) and not injection. | Not scheduled |
| H2 | Medium | The e2e tests never exercise the agent path, not even with a stub model. The harness e2e writes SQL straight into the database (`e2e/database.ts`), which couples them to the schema. | Not scheduled |
| H3 | Medium | There is no CI: the only gate is a local pre-commit hook that anyone can skip. No `.env.example`, no Dockerfile, no deploy notes. | Partly scheduled (SECURE step 4, RUNTIME) |
| H4 | Medium | `pyproject.toml` declares 37 runtime dependencies (178 locked packages), mostly the course's tooling: `boto3`, `gradio`, `plotly`, `pandas`, `ollama`, `langfuse`, `claude-agent-sdk`, `deepagents`. The product's code imports about 7. A `server` group exists and nothing uses it. That is image size and attack surface. | Partly scheduled (SECURE step 4) |
| H5 | Low | No dependency or secret scanning. | Not scheduled |

### I. Code smells

- `backend/main.py` is both the HTTP layer and the orchestrator (`execute_attempt`, `execute_queue`). It imports `agent`, which calls the backend back over HTTP. That loop works, but it ties the two together and is why tests patch module globals.
- `rules.py` (283 lines) holds decisions, payments, sign-off, attempts, recovery and a presentation function (`result_line`). `decide` is the most complex function in the code (complexity 7).
- Control flow rides on strings: `unfinished_reason` returns a sentence that is also stored as a step.
- The same literals appear in Python and TypeScript: step kinds, states, to-do statuses. Adding one means touching five files.
- `agent/queue.py` shares its name with the standard library module. Harmless from the repo root, a trap for a script run inside `agent/`.
- `Outcome` is used for three different things: a state change, a payment result and a `work_claim` result.
- Ruff's extra rules found nothing else of note: two intentional `except Exception`, no dead code, no complexity above 7.

## Evidence

All run against a throwaway database, with the real backend code.

1. `POST decision approve payout = 10 × amount claimed` on `CLM-1001` returns 200 and `waiting_for_sign_off`.
2. `payout: NaN` returns 500. `payout: Infinity` returns 200 and sends the claim for sign-off.
3. `explanation: ""` and `policy_section: ""` return 200.
4. With no credentials, `POST sign-off {"handler_id": "HND-01"}` on a held claim returns 200 and "Dana Whitfield signed off. Paid $1,250.00."
5. `call()` on a plain-text 500 raises `JSONDecodeError`. The claim agent's `decide_claim` tool takes `claim_id` as a model-supplied argument.
6. Eight concurrent payments of the same claim: `[200, 409, 409, 409, 409, 409, 409, 500]`. Eight concurrent decisions came out `[200, 409 × 7]`, and six concurrent run starts came out `[202, 409 × 5]`. Those two passed here, but nothing but timing makes them pass: `runs.create` has no constraint behind it.
7. The queue has a customer (`CUS-3005`) with two claims, and `get_claim_history` returns only `customers.json`'s `previous_claims`.
8. A 5 MB `add_step` returns 200.

## Where this leaves the roadmap

The later briefs will change the shape of the system a lot: Postgres and SQLAlchemy (DURABLE), a LangGraph claim agent in its own service (DURABLE), a code dispatcher in place of the queue agent (SWARM), sign-in (IMPROVE), an API key for the agent and a Lambda build (SECURE). Findings C2, C4, C6, C7, C8 and B1 will be overtaken by them. Putting effort into the SQLite concurrency settings, the in-process background tasks or the queue agent's loop would be wasted.

What those briefs do not mention is the subject of the plan: validating decisions, making writes atomic, confining the agent to its claim, the customer history, configuration, logging, and a cleaner structure for the code that the briefs will build on.
