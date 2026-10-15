# Status

The claims pilot, built phase by phase from `docs/PLAN.md`.

## Built

- `backend/`: FastAPI and SQLite. The database is `backend/undercover.db` (git-ignored), created and seeded from `data/` on first start. Read operations: `list_claims`, `get_claim`, `get_policy`, `get_claim_history` (no contact details), `get_policy_wording`, `get_instructions`, `list_handlers`, `list_steps`.
- `agent/`: still the hello-world entry point `run(claim_id, thread_id)`.
- `dashboard/`: still one page that shows whether the backend is up.
- `e2e/`: one test of that page.

## Run it

- `uv sync` and `npm install`, then `npm run dev` for the backend (port 8000) and the dashboard (port 5173).
- `npm run check` runs every check.
- Delete `backend/undercover.db` to start again from the seed data.
- Tests use their own database through `UNDERCOVER_DB`.
