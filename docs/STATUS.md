# Status

The claims pilot is built, following `docs/PLAN.md`: a backend, a dashboard for the handlers and the UnderCover Agent.

## Run it

- `uv sync` and `npm install`. Put `OPENROUTER_API_KEY` in `.env`. Set `MODEL_NAME` to use another model, such as `openrouter/free`.
- `npm run dev` starts the backend (port 8000) and the dashboard (port 5173, open http://localhost:5173).
- The database is `backend/undercover.db` (git-ignored), created and seeded from `data/` on first start. Delete it, with the backend stopped, to start again from the seed data.
- Run the agent from the command line while the backend runs: `uv run -m agent` works every waiting claim as one run, `uv run -m agent --limit 5` works at most five and leaves the rest waiting, and `uv run -m agent CLM-1001` works one claim. A single claim can also be run from the dashboard ("Run agent").
- `npm run check` runs every check: ruff, pyright and pytest at 80% coverage; ESLint, Prettier, tsc and vitest at 80% coverage; Playwright. `npm run e2e` runs only Playwright, on its own backend (port 8001), dashboard (port 5174) and a new database in the temp folder, so it never touches the dev database.

## What is built

- `backend/`: FastAPI and SQLite. Every operation has an `operation_id`, a one-line docstring and no optional inputs.
  - Reads: `list_claims`, `get_claim`, `get_policy`, `get_claim_history` (earlier claims only, no contact details), `list_document_sections`, `get_document_section` (the policy wording and the claims manual, by section), `get_instructions` (for `queue` or `claim`), `list_handlers`, `list_steps`.
  - Writes: `decide_claim`, `pay_claim`, `sign_off_claim`, `add_step`, `run_agent`, and for the queue agent's runs `start_run` (a required `limit`), `get_latest_run`, `write_todos`, `work_claim` (a fresh claim agent on one claim, answering in one line) and `finish_run` (refused while the run has missed a waiting claim). A run counts the claims it starts and refuses a new one past its limit; a claim waiting for a retry never counts again.
  - The rules are in `backend/rules.py`. A decision is recorded once. An approval over $1,000 (`payout_approval_limit` in `data/company.json`) ends in `waiting_for_sign_off`, and `pay_claim` is refused with a 409 until `sign_off_claim`, which records the handler and makes the payment. A payment must equal the approved payout and happens once.
  - A claim the agent is working on stays `in_progress` until its attempt ends, then settles to `decided` or `waiting_for_sign_off`. Each try is an attempt. An attempt that crashes, ends with no decision, or ends with an approval of $1,000 or less that is not paid puts the claim back to `waiting` with an error step; a second failed attempt leaves it in the state `left_for_handler`, which the agent cannot change. On start, claims left `in_progress` by a stopped backend are settled the same way. The database changed in the harness build: delete `backend/undercover.db` with the backend stopped to reseed.
  - The agents' instructions are `backend/instructions_claim.md` and `backend/instructions_queue.md`. The backend loads only `claims.json`, `policies.json`, `customers.json`, `handlers.json`, `company.json`, `policy_wording.md` and `claims_manual.md` from `data/`; a test checks it. It never reads `data/evals/`, `data/security/` or `knowledge.json`.
- `agent/queue.py`: the queue agent, `run_queue(run_id)`. Its four tools are `list_waiting_claims`, `write_todos`, `work_claim` and `finish_run`. It plans a to-do list, hands each claim to a fresh claim agent through `work_claim`, and is reminded up to twice if it stops before `finish_run`. It never reads a claim.
- `agent/main.py`: the claim agent, `run(claim_id, thread_id)` fetches its instructions from the backend, works the claim with seven tools (`get_claim`, `get_policy`, `get_claim_history`, `list_document_sections`, `get_document_section`, `decide_claim`, `pay_claim`), reading the wording and the manual by section (each section read is a `read` step), with retries and a call limit; the default model is `deepseek/deepseek-v4.1-flash` and posts every step to the backend. A refused call comes back to the model as text. It reaches the backend only over HTTP; a test checks that it reads no files.
- `dashboard/`: React, Vite and TypeScript, in the brand's look, light and dark. The Claims view has the queue (a count for each state, every claim) and the selected claim in full. A waiting claim has "Run agent". While the agent works, "What the agent did" lists its steps, refreshed every 2 seconds. A claim waiting for sign-off has a handler picker and "Sign off and pay". The Instructions view shows the agent's instructions, read only. The queue refreshes every 2 seconds.
- `e2e/`: Playwright tests of the dashboard. They set up decisions and steps through the API and never run the agent.

## Checked live

On 15 October 2026 the agent, with the default model, ran on five claims: CLM-1001, CLM-1002, CLM-1011, CLM-1016 and CLM-1019. All finished. Four were decided and the payments under $1,000 were made. CLM-1011, at $1,110, stopped at `waiting_for_sign_off` with no payment. Each explanation was two sentences and named a policy section. The results were not compared with `data/evals/`.

## Open items

- Payouts vary between runs of the same claim (CLM-1002 came out at $400 and at $580), because the model does the arithmetic. Scoring the agent against `data/evals/` and any fix for the arithmetic are for a later build.
- The Instructions view shows the Markdown marks as they are written.
- "Run all waiting" is only on the command line (`--all`).
- The claims manual, sign-in, real payments and messages to customers are not built, as the spec says.
