# Status

The claims pilot, built phase by phase from `docs/PLAN.md`.

## Built

- `backend/`: FastAPI and SQLite. The database is `backend/undercover.db` (git-ignored), created and seeded from `data/` on first start. Read operations: `list_claims`, `get_claim`, `get_policy`, `get_claim_history` (no contact details), `get_policy_wording`, `get_instructions`, `list_handlers`, `list_steps`. Write operations: `decide_claim`, `pay_claim`, `sign_off_claim`, `add_step`, `run_agent`.
- The rules are in `backend/rules.py`: a decision is recorded once; an approval over the $1,000 limit moves the claim to `waiting_for_sign_off`; `pay_claim` is refused (409) for that claim until `sign_off_claim`, which records the handler and makes the payment; the payment must equal the approved payout and happens once.
- `agent/`: the UnderCover Agent. `run(claim_id, thread_id)` fetches its instructions from the backend, works the claim with six tools (`get_claim`, `get_policy`, `get_claim_history`, `get_policy_wording`, `decide_claim`, `pay_claim`) and posts each step to the backend. A refused call comes back to the model as text. Its instructions are `backend/instructions.md`.
- Start it from the command line with `uv run -m agent CLM-1001` or `uv run -m agent --all` (the backend must be running), or with `POST /api/claims/{id}/run`. It needs `OPENROUTER_API_KEY`, and uses `MODEL_NAME` if set.
- `dashboard/`: React, Vite and TypeScript in the brand's look, light and dark. The Claims view has the queue (a count for each state and every claim) and, beside it, the selected claim in full: story, items, documents, policy, earlier claims and, once decided, the decision with its reasons and payment. A waiting claim has a "Run agent" button. While the agent works, "What the agent did" lists its steps, polled every 2 seconds, and stops when the claim is decided. A claim waiting for sign-off has a handler picker and "Sign off and pay". The Instructions view shows the agent's instructions, read only. The queue refreshes every 2 seconds. Types are in `src/types.ts`; loading and polling are in `src/api.ts`.
- `e2e/`: Playwright tests of the dashboard on their own backend (port 8001) and dashboard (port 5174), with a new database in the temp folder for each run. They set up decisions and steps through the API; they never run the agent.

## Run it

- `uv sync` and `npm install`, then `npm run dev` for the backend (port 8000) and the dashboard (port 5173).
- `npm run check` runs every check.
- Delete `backend/undercover.db` to start again from the seed data.
- Tests use their own database through `UNDERCOVER_DB`. `npm run e2e` starts its own servers and does not touch `npm run dev`'s database.
