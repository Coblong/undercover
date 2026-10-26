# Status

The claims pilot is built, following `docs/PLAN.md`: a backend, a dashboard for the handlers and the UnderCover Agent. The harness build, following `docs/PLAN_HARNESS.md`, has the agent run the queue from a to-do list, hand each claim to a fresh claim agent, look up the wording and the claims manual by section, and recover from failures.

## Run it

- `uv sync` and `npm install`. Put `OPENROUTER_API_KEY` in `.env`. Set `MODEL_NAME` to use another model, such as `openrouter/free`.
- Settings are read from the environment when needed (`agent/settings.py`): `BACKEND_URL` (default http://localhost:8000, the address the agent uses to reach the backend, so set it if the backend is on another port), `OPENROUTER_API_KEY` and `MODEL_NAME`. A real environment variable beats `.env`. `npm run dev` starts the backend on port 8000 with `--env-file .env`; `uv run -m agent` loads `.env` itself.
- Logs: the backend logs to the terminal. A crashed run is logged with its traceback, and each ended attempt logs one line (claim, run, state, failed, seconds). The team sees only the kind of error in a step.
- `npm run dev` starts the backend (port 8000) and the dashboard (port 5173, open http://localhost:5173).
- The database is `backend/undercover.db` (git-ignored), created and seeded from `data/` on first start. Delete it, with the backend stopped, to start again from the seed data. For a trial with the real model, reseed first and cap the run at five claims.
- Run the agent from the command line while the backend runs: `uv run -m agent` works every waiting claim as one run, `uv run -m agent --limit 5` works at most five and leaves the rest waiting, and `uv run -m agent CLM-1001` works one claim. The dashboard has "Run the queue" (a number of claims) and, on a waiting claim, "Run agent".
- `npm run check` runs every check: ruff, pyright and pytest at 80% coverage; ESLint, Prettier, tsc and vitest at 80% coverage; Playwright. `npm run e2e` runs only Playwright, on its own backend (port 8001), dashboard (port 5174) and a new database in the temp folder, so it never touches the dev database.

## What is built

- `backend/`: FastAPI and SQLite. Every operation has an `operation_id`, a one-line docstring and no optional inputs.
  - Reads: `list_claims`, `get_claim`, `get_policy`, `get_claim_history` (by claim id: the customer's earlier claims plus every claim in this system filed before it, decided or open; no contact details), `list_document_sections`, `get_document_section` (the policy wording and the claims manual, by section), `get_instructions` (for `queue` or `claim`), `list_handlers`, `list_steps`.
  - Writes: `decide_claim`, `pay_claim`, `sign_off_claim`, `add_step`, `run_agent`, and for the queue agent's runs `start_run` (a required `limit`), `get_latest_run`, `write_todos`, `work_claim` (a fresh claim agent on one claim, answering in one line) and `finish_run` (refused while the run has missed a waiting claim). A run counts the claims it starts and refuses a new one past its limit; a claim waiting for a retry never counts again.
  - The modules: `main.py` (the app, start-up and error handlers), `api_claims.py` and `api_runs.py` (the routes), `deps.py` (the connection and the agents a route is given), `decisions.py` (decisions, payments and sign-off, with the rules), `attempts.py` (steps, attempts, retries and recovery), `runs.py` (a run, its limit and to-do list), `work.py` (running the agents, ending an attempt, logging), `queries.py`, `documents.py`, `instructions.py`, `models.py` and `db.py` with `schema.sql`. A test replaces the agents with FastAPI dependency overrides (`work.claim_agent` and `work.queue_agent`) through the `agents` fixture in `conftest.py`.
  - The rules are in `decisions.py` and `attempts.py`. Every write that reads first runs in one `BEGIN IMMEDIATE` transaction (`backend/db.py`), so two requests cannot both pass a check, and the database backs this with foreign keys, one open attempt per claim and one running run. A decision is refused (422) when its payout is not finite, is not above 0 for an approval, is more than the amount claimed, or when its explanation or policy section is empty or the section is not one of the wording's or the manual's. Texts have length limits. A decision is recorded once. An approval over $1,000 (`payout_approval_limit` in `data/company.json`) ends in `waiting_for_sign_off`, and `pay_claim` is refused with a 409 until `sign_off_claim`, which records the handler and makes the payment. A payment must equal the approved payout and happens once.
  - A claim the agent is working on stays `in_progress` until its attempt ends, then settles to `decided` or `waiting_for_sign_off`. Each try is an attempt. An attempt that crashes, ends with no decision, or ends with an approval of $1,000 or less that is not paid puts the claim back to `waiting` with an error step; a second failed attempt leaves it in the state `left_for_handler`, which the agent cannot change. On start, claims left `in_progress` by a stopped backend are settled the same way. The database changed in the harness build: delete `backend/undercover.db` with the backend stopped to reseed. The quality build (`docs/PLAN_QUALITY.md`) added foreign keys and indexes, which a new database gets, so reseed after pulling it.
  - The agents' instructions are `backend/instructions_claim.md` and `backend/instructions_queue.md`; `backend/instructions.py` fills in the approval limit, the filing window and the repeat-claim rule from `data/company.json` when it serves them. The backend loads only `claims.json`, `policies.json`, `customers.json`, `handlers.json`, `company.json`, `policy_wording.md` and `claims_manual.md` from `data/`; a test checks it. It never reads `data/evals/`, `data/security/` or `knowledge.json`.
- `agent/queue.py`: the queue agent, `run_queue(run_id)`. Its four tools are `list_waiting_claims`, `write_todos`, `work_claim` and `finish_run`. It plans a to-do list, hands each claim to a fresh claim agent through `work_claim`, and is reminded up to twice if it stops before `finish_run`. It never reads a claim.
- `agent/main.py`: the claim agent, `run(claim_id, thread_id)` fetches its instructions from the backend, works the claim with seven tools built for that claim by `claim_tools(claim_id)` (`get_claim`, `get_policy`, `get_claim_history`, `list_document_sections`, `get_document_section`, `decide_claim`, `pay_claim`), none of which takes a claim, policy or customer id, so the model cannot name another claim (the backend itself does not check this yet), reading the wording and the manual by section (each section read is a `read` step), with retries and a call limit; the default model is `deepseek/deepseek-v4.1-flash` and posts every step to the backend. A refused call comes back to the model as text. It reaches the backend only over HTTP; a test checks that it reads no files.
- `dashboard/`: React, Vite and TypeScript, in the brand's look, light and dark. Its API types are generated from the backend (`npm run types` writes `dashboard/src/api-types.ts` from `uv run -m backend.openapi`; `npm run check:python` fails if it is out of date, so run `npm run types` after changing `backend/models.py`). A refused action shows the backend's own reason, polling pauses while the tab is hidden, and list keys stay unique when a text repeats. The Claims view has a "Run the queue" panel (a number of claims, a button, and the latest run with its to-do list and summary, refreshed every 2 seconds while it runs), the queue (a count for each of five states, every claim) and the selected claim in full, with "What it read" listing the sections the agent looked up. A waiting claim has "Run agent". While the agent works, "What the agent did" lists its steps, refreshed every 2 seconds. A claim waiting for sign-off has a handler picker and "Sign off and pay". The Instructions view shows the queue agent's and the claim agent's instructions, read only. The queue refreshes every 2 seconds.
- `e2e/`: Playwright tests of the dashboard. They set up decisions and steps through the API, and a run directly in the test database, and never run the agent.

## Checked live

On 26 October 2026, after reseeding, `uv run -m agent --limit 5` ran on the default model (`deepseek/deepseek-v4.1-flash`, low reasoning). The queue agent wrote a to-do list of five claims, worked them in its own order, ticked each off and finished with a summary. Nothing failed or was retried, and the other 20 claims stayed `waiting`. Each claim's page showed the 10 to 14 sections of the wording and the manual the agent read.

| Claim | Decision | Payout |
|---|---|---|
| CLM-1004 | approve | $186.00, paid |
| CLM-1001 | approve | $420.00, paid |
| CLM-1012 | decline | $0.00 |
| CLM-1003 | approve | $3,750.00, waiting for sign-off, no payment |
| CLM-1016 | decline | $0.00 |

The results were not compared with `data/evals/`.

After the quality build (`docs/PLAN_QUALITY.md`) the same run, reseeded and capped at five, finished with no failed attempt and no call refused by the new checks: CLM-1003 (approved $3,750.00, held for sign-off), CLM-1004 (approved $186.00, paid), CLM-1013 (declined) and CLM-1023 and CLM-1025 (referred). Before the instructions told the model to give the policy section as a bare number and title, three of five first decisions were refused (a note had been added to the section name); the agent corrected each one itself.

## Open items

- Payouts may vary between runs of the same claim, because the model does the arithmetic. Scoring the agent against `data/evals/` and any fix for the arithmetic are for a later build.
- The claim agent reads many sections for each claim (10 to 14 in the live check), more than the instructions strictly need. Narrowing that would cut cost.
- A claim left for a handler stays there: handlers have no way yet to return it to the queue or decide it from the dashboard.
- The Instructions view shows the Markdown marks as they are written.
- Claims are worked one at a time. Several at once is not built.
- Sign-in, real payments and messages to customers are not built, as the spec says.
