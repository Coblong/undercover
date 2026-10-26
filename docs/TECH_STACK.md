# Tech stack

- `backend/`: FastAPI and SQLite. Every API operation has an `operation_id`, a one-line docstring and no optional inputs. Paying a claim is a separate operation that takes the amount. It holds the runs, their to-do lists and every attempt on a claim, and it enforces a run's limit and the retry rules.
- `agent/`: LangChain's `create_agent` on OpenRouter, with no other agent framework. The queue agent works a run: it plans a to-do list, hands each claim to a claim agent and finishes with a summary. The claim agent is a new agent for each claim. Both reach claims, documents and their instructions only through the backend's API, and the claim agent looks up the policy wording and the claims manual by section. LangChain's retry and call-limit middleware keep a failing step from stopping a run. Entry points: `run_queue(run_id)` for a run and `run(claim_id, thread_id)` for one claim. They use `deepseek/deepseek-v4.1-flash` with low reasoning effort by default, or the model in the environment variable `MODEL_NAME` if set, such as `openrouter/free`.
- `dashboard/`: React, Vite and TypeScript. Its API types are generated from the backend's OpenAPI document with `npm run types`.
- `e2e/`: Playwright tests.
- `data/`: the business data the backend starts from. The agent never sees `data/evals/`, which holds the answer key.

Python 3.12 with uv, all in the root `pyproject.toml`. Node 26. The OpenRouter key is `OPENROUTER_API_KEY` in `.env`.
