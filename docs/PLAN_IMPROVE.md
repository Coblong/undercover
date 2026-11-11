# Plan: an improver for the claim agent, and handlers who sign in

Builds `docs/IMPROVE.md`: handlers who sign in with Supabase Auth, the claim agent's configuration in versions, a judge for the agent's explanations, an improver that proposes changes and a recipe for a handler to approve, a spending limit for each round, a Settings page, and a dashboard that looks like enterprise software. It follows `docs/SPEC.md`, `docs/TECH_STACK.md`, `docs/CODE_STANDARDS.md`, `docs/PROCESS.md` and `docs/BRAND.md`.

SIDENOTE: For consistency with the course, please read `labs/week2day5.ipynb` and build the judge, the configuration's versions and the improver in the same style as the lab. Where the lab and this brief differ, follow the brief.

I read the lab. Its judge is Jev (`typesafe/jev-1.13`) through OpenRouter's Decisions endpoint with one `score` question (grades F, C, B, A, numbered 0 to 3) and two `noul` questions (`same_grounds`, `same_section`). The letter is the level with the highest probability and the grade is Jev's `score`. A configuration is `{version, instructions, model, reasoning_effort}`. A version is scored by the averages of its runs on each set. The rule is `compare` and `keep_or_roll_back`: objectives first, then anything worse than the tolerance, then at least one measure better than the noise. The improver is a `create_agent` on Luna at medium with four tools (`read_results`, `propose_change`, `run_evaluation`, `propose_recipe`), and `HumanInTheLoopMiddleware` pauses it on the two propose tools. The recipe's last step improves the recipe. This build follows that shape. It differs where the brief does:

- The configuration, the recipe, the scores and the rounds are records in the backend's database, not Python lists. The backend works out the diffs and applies the rule.
- Grading, the rule and the spending limit run in the backend, because it holds the handlers' decisions. The improver reaches the backend through a client of its own.
- A handler approves or rejects on the dashboard. The pause survives a restart, on a Postgres checkpointer.
- A round has a spending limit that covers the improver, the evaluations and the judge, and it can be stopped.

## What exists

- **Backend:** FastAPI and SQLAlchemy Core on Supabase or SQLite. `runs` holds dispatches and evaluations (`kind`), and `claims.evaluation_id` marks an evaluation's copies. `answer_key` holds the handlers' decisions on both sets (decision, payout, `tiny`), and `held_out_claims` the held-out claims. Only `backend/evaluations.py` and seeding read them, and a test checks that. `scoring.py` works out the measures. The `handlers` table has no email column, though `data/handlers.json` has the emails. All three handlers have `can_approve_over_limit: true`.
- **Agent:** `main.py` (the claim agent and `work_claim`), `dispatcher.py`, `service.py` (port 8002, with the one-run rule as `_gate`, a `threading.Lock`), `__main__.py`, `settings.py` and `tracing.py`. The agent's client for the backend is `main.call` and `main.backend()`. There is no `agent/api.py`.
- **Dashboard:** Claims, Scoreboard and Instructions views, switched by buttons in `App.tsx`, with no sign-in. `styles.css` sets dark mode from `prefers-color-scheme` alone.
- **Tests:** `e2e/agent.mjs` stands in for the agent's service on port 8003. Each spec opens the dashboard with no sign-in.
- **Packages:** `pyjwt[crypto]` and `openrouter` are installed. `@supabase/supabase-js` is not. LangChain is 1.4.2 and has `HumanInTheLoopMiddleware`, `ModelCallLimitMiddleware` and the `after_model` hook. `.env` already has the three Supabase lines from the lab.

## Where this build differs from the brief's notes

The notes come from Ed's `week2day4-end` branch. I followed their intent.

- **`agent/api.py` does not exist here.** The claim agent's client is `main.call`. The improver gets a module of its own, `agent/improver_client.py`. A new test scans `agent/main.py` for the paths it calls and fails on any path for evaluations, rounds, settings, configurations or scores. That replaces `test_the_agent_cant_read_the_scoreboard`.
- **`CODE_STANDARDS.md` has 8 rules here.** The brief names rules 7, 13 and 14. I'll update rule 7 (the parts of the agent) and add two rules: the dashboard's only Supabase client lives in one module, and the agent's instructions are written as goals, not as a step-by-step script (the recipe is the one place that may be a list, because it is data the improver follows). I did the same in the swarm build.
- **`handler_decisions` is the `answer_key` table here.** Seeding adds `reason` and `reference` to it from the three files in `data/evals/` it already reads. The brief lets me open them for this.
- **The one-run rule has one message today** ("A dispatch or an evaluation is already running"). It will name what is running.
- **Version 1's instructions are `backend/instructions_claim.md`,** a template with `{approval_limit}`, `{filing_days}`, `{repeat_claims}` and `{repeat_months}`.

## Design decisions

These shape every phase. Please review them first.

1. **Sign-in.**
   - The dashboard signs in with `signInWithPassword` in one module (`dashboard/src/supabase.ts`) and sends the session's access token as `Authorization: Bearer` on every request. A 401 sends the page back to sign-in.
   - The backend checks the token with PyJWT's `PyJWKClient` against `{VITE_SUPABASE_URL}/auth/v1/.well-known/jwks.json`, audience `authenticated`, in one module (`backend/auth.py`). The address is read in one function, once for each process. Tests clear it.
   - The check is a dependency that decodes the token and holds no database connection (rule 8). The route then finds the handler by email on its own `with_db` connection. A token that does not verify is a 401. A valid sign-in whose email is not a handler is a 403. A handler without the right is a 403 that names them and says what they can't do.
   - Handler actions: `start_dispatch`, `start_evaluation`, `sign_off_claim`, `start_round`, `stop_round`, `approve_proposal`, `reject_proposal` and `update_settings`. The last five need `can_approve_over_limit`. Sign-off needs it too, as today.
   - **Reads stay open,** as do the agent's and the service's calls. The brief asks for the backend to check the handler on actions. Sign-in for reads and an API key come in Week 3.
   - A new operation, `get_me`, gives the dashboard its signed-in handler. `sign_off_claim` takes no body: the signed-in handler is the one who signs off. The handler picker goes.
   - `data/handlers.json` sets `can_approve_over_limit` to `false` for Marcus Oyelaran and Priya Raman. Seeding stores each email.
2. **The configuration and the recipe are versioned records.**
   - `configurations` holds a version number, the instructions, the model, the reasoning effort, the reason, who proposed it (`seed` or the round), who approved it and when. `current_configuration` is a one-row table that points at the current version. A rolled-back version stays in the table, so the history is complete.
   - `recipes` holds the same for recipes. The current recipe is the latest approved one, since a recipe is never rolled back.
   - Version 1 of each is seeded: the instructions from `instructions_claim.md` with `openai/gpt-6-luna` at `low`, and the recipe from the lab's first recipe in a file beside it (`backend/recipe_improver.md`).
   - **The stored instructions keep their `{placeholders}`.** The backend fills in the four known names with `str.replace` when it serves the instructions to the claim agent, using the settings' approval limit and `company.json`. `str.format` would choke on any other braces the improver writes. The improver sees the stored text and is told that the words in braces are filled in by the backend and must stay.
   - The backend works out each diff with `difflib.unified_diff` and returns it as typed lines (`add`, `remove`, `context`). The dashboard colors them.
   - The claim agent fetches its version from `get_run_configuration` (`/api/runs/{run_id}/configuration`) or, for work outside a run (one claim by name, a sign-off resuming), `get_current_configuration`. Neither holds scores. They replace `get_instructions`.
   - `runs` gains `config_version`. A dispatch and a Scoreboard evaluation take the current version when the backend creates them. A round's evaluation takes the version under test. The service no longer sends the model or effort: the backend fills them from the version.
   - **Free models.** `MODEL_NAME` applies at seeding (version 1 uses it at effort `default`) and to the models allowed. The reasoning effort is `default`, not `none`, as in the free lab. Reseed after changing `MODEL_NAME`.
3. **A version's scores and the rule.**
   - `backend/improvement.py` is pure code: the scores, `compare` and `keep_or_roll_back`. A version's scores on a set are the averages of its finished, whole-set evaluations that rounds ran (`runs.round_id` is set and `tiny` is false). The measures averaged are accuracy, grade, cost per claim and time per claim. The grade averages over the runs that have one, and is none when none does.
   - The rule is the lab's. Cost and time are compared as fractions of the old value, accuracy and the grade as differences. A version with no grade is rolled back. The objectives apply to the average of the new version's held-out runs.
   - "Accuracy" is the lab's `accuracy` (decision right and payout within a dollar), not `decision_accuracy`. The Scoreboard shows both.
4. **The judge** (`backend/judge.py`, a module of its own).
   - The call is written once: `OpenRouter(...).alpha.decisions.create(model="typesafe/jev-1.13", state=..., questions=...)`. It has `server_url`, `timeout_ms` and `retries`, which I checked on the installed SDK. `server_url` is read once from `OPENROUTER_SERVER_URL` (an origin, default `https://openrouter.ai`), `timeout_ms` is 10,000 and retries are off.
   - The state is the handler's decision, reason and reference beside the agent's decision, explanation and section. The letter is `GRADES[int(argmax(probabilities))]`. The two checks stay as probabilities in the module and are stored as yes or no.
   - Grading happens when an evaluation finishes, in `end_run`: the claims in parallel on a thread pool of 25, outside any database transaction, then one write. Each call is capped at 10 seconds, so finishing stays under 20, inside the agent's 30-second wait.
   - A claim the agent didn't decide gets F and a grade of 0 without a call. A claim the judge couldn't grade is stored as not graded and left out of the average. A stopped or failed evaluation is not graded.
   - `evaluation_grades` holds a row for each claim: grade, letter, the two checks, the judge's cost. The evaluation's `judge_cost` is their sum, kept apart from `cost_per_claim`.
   - With `MODEL_NAME` set the judge is not called and every claim shows as not graded.
5. **Settings.**
   - `settings` holds one row for each setting, with its current value. `setting_changes` is the append-only record of who changed what and when. Seeding takes the approval limit from `company.json` and the rest from a new file, `data/improvement.json`: objectives (accuracy 0.6, grade 1.0), noise and tolerance from the lab, a spending limit of $0.50, and 25 agents at once.
   - The models allowed are in `data/models.json`, read from the file, never the database: the three models from the lab with their reasoning efforts and prices as numbers per million tokens. With `MODEL_NAME` set the list is that one model at `default` and a price of 0.
   - **A new approval limit applies to the decisions recorded after it.** `decisions` gains the limit in force when it was recorded, and `pay` and `needs_sign_off` read that, not the setting.
   - The approval limit in the claim agent's instructions comes from the setting.
   - The appearance is the one setting kept in the browser (`localStorage`).
6. **Rounds.**
   - `rounds` holds the state (`open`, `finished`, `stopped`), the stage, the progress counter, a snapshot of the settings from when it started, the recipe version, who started it, the version tried, the improver's reason, the held-out scores before and after, the verdict and why, a pending proposal, and when it started and ended. A unique partial index allows one open round.
   - `round_decisions` holds who approved or rejected what and when, and the rejection's reason. `round_calls` holds the improver's model calls with tokens and cost. `model_calls` already holds the claims' calls, and a round's evaluations reach it through `runs.round_id`.
   - **Stages:** `first_evaluations` (1, 2 or 3 of 3), `improver_working`, `change_waiting`, `evaluating` (1, 2 or 3 of 3), `verdict`, `recipe_waiting`, then `finished` or `stopped`. The service records each as it happens and the page reads it.
   - **The improver's view** is built by the backend (`get_improver_view`) in the lab's shape: the current version, its scores on both sets, from the latest whole-set evaluation of the current version the wrong claims and the five right ones with the lowest grades (each with the handler's decision, payout, reason and reference beside the agent's, the judge's grade and letter and the two checks), the earlier rounds, the objectives, noise, tolerance and the rule, what's left of the spending limit, and the models allowed. From the held-out set it holds scores only. A test checks that no held-out claim id, summary or reason appears in it.
   - A round in which the improver proposes nothing finishes with no version tried. A version that was approved but never evaluated is rolled back when the round ends.
7. **The improver** (`agent/improver.py`, the sixth part of the agent) and its client (`agent/improver_client.py`).
   - A `create_agent` on Luna at `medium` (or `MODEL_NAME`), with `parallel_tool_calls=False` in its model settings and a `ModelCallLimitMiddleware` on the round's thread. Its instructions wrap the recipe and are goals: what the claim agent works from, and that a change never names a claim.
   - The tools are the lab's four, with the lab's names. They are built for one round, so the model has no id to change. `read_results` reads the improver's view. The two propose tools carry the proposal and, once approved, report the version the service recorded. `run_evaluation` works the evaluations.
   - **A proposal outside the models allowed goes back to the improver without reaching a handler.** `HumanInTheLoopMiddleware` interrupts before a tool runs, so the check can't be in the tool body. A small middleware with an `after_model` hook, placed so it runs before the approval's hook, replaces an invalid call with an error message and jumps back to the model. I will prove the order in a test with the stub model, and if the order can't be made to work, the fallback is one `wrap_model_call`.
   - **Checkpointer:** a `PostgresSaver` from the service's pool (`new_saver()`), saving with `durability="sync"`. Each round's thread id is new, `round-<id>-<random>`, so a reseed never meets an old round's checkpoints.
   - **The approval path.** The dashboard asks the backend, which checks the handler and passes it to the service with the handler's name and id, and passes a refusal back. The service takes the one-run rule first, then records the decision and the handler with the backend (which creates the version, with its approver), then resumes the improver. The backend records nothing of its own. A rejection resumes with `{"type": "reject", "message": reason}`, so the improver sees the reason and may propose again.
   - **The service's stretches.** Starting a round, approving and rejecting each take the one-run rule for the stretch of work that follows, and give it back when the round pauses for a handler or ends. The improver's evaluations are part of that stretch and never refused because of it. They run through the dispatcher in the service, each in a fresh `contextvars.Context`, since a graph compiled with no checkpointer otherwise saves into its caller's thread. While a round waits, dispatches and evaluations can run.
   - **The gate** gains a label, so a refusal says what is running ("A dispatch is running", "An evaluation is running", "The improver is working").
   - **Stopping.** `stop_round` marks the round stopped in the backend, which then refuses anything the round records. The service starts no more claims or model calls for it, and gives up the gate when the work in hand ends. A round stopped before its verdict leaves the current version as it was, and one stopped after keeps it.
   - **A restart.** At start-up the service marks the rounds that were mid-work (first evaluations, improver working, evaluating) as stopped, as it does for runs. A round waiting for a handler stays open and resumes from its checkpoint.
8. **The spending limit.**
   - A round's spend is the improver's calls plus its evaluations' model costs plus the judge's costs.
   - **The estimate** (`backend/improvement.py`): an evaluation's claims times the highest cost per claim seen for its model across all evaluations. For a model with no runs it is the current model's, scaled by the larger of the ratios of their input and output prices. With no run on any model yet there is nothing to estimate from, so the evaluation is allowed and the hard stop covers it. The estimate leaves out the judge, as the brief says. The spend counts it.
   - **Refusal:** the improver's `run_evaluation` is refused with a message when the estimate is more than what's left. The version is then rolled back, "the round couldn't afford to evaluate it".
   - **The hard stop:** `start_claim` refuses to start a claim in a round's evaluation once the round's spend has reached the limit. An evaluation that ends with copies it never started is recorded as `stopped`, not `finished`. A version whose evaluation was stopped is rolled back.
   - A first evaluation that the limit stops ends the round as stopped by the limit.
9. **The tests' stand-ins.** The backend reads the addresses of Supabase and of the judge's endpoint once from the environment. The unit tests monkeypatch the signing-key lookup and the judge call. The end-to-end tests start two stand-ins beside the agent's:
   - `e2e/auth.mjs` signs tokens with a key it generates, serves its public keys at `/auth/v1/.well-known/jwks.json`, answers `/auth/v1/token`, and answers the browser's cross-origin checks.
   - `e2e/judge.mjs` answers the Decisions endpoint with scripted grades.
   - `playwright.config.ts` points the backend and the dashboard at them. `dashboard/vite.config.ts` sets `envDir` to the repo root only when Vitest isn't running, so `npm run dev` reads `.env` and the unit tests don't. Vite lets real environment variables win over `.env`, which is how Playwright overrides it.
10. **The dashboard.**
    - **Shell:** a header with the name, the navigation with the current page marked, the signed-in handler and Sign out. A `PageHeader` (title, one line on what the page is for, actions) on every page. Pages: Claims, Scoreboard, Improvement, Configuration (replaces Instructions) and Settings.
    - **Shared parts, built in Phase 1 so every later page uses them:** `PageHeader`, `Status` (a color and a word), a `Table` with right-aligned numbers and units, `ConfirmDialog` (the dashboard's own `<dialog>`), and loading, empty and error states that say what to do next.
    - **Theme:** colors are set from a `data-theme` attribute (`light`, `dark`, or none for the computer's setting) as well as the media query, and a small inline script in `index.html` sets it before the first paint, so nothing flashes.
    - **The diff view** is one component for versions and recipes. **The chart** is hand-drawn SVG: two aligned small charts of the held-out accuracy and grade of each version tried, with the objective's floor as a line and kept or rolled back as a shape and a word.
    - **Confirmations** before: starting a round (it spends up to the limit), stopping one, approving, rejecting (with a reason) and saving settings.
    - **Brand:** no one-sided borders and no thin lines across the top. Tables use row shading, not rules.
    - Existing pages get the shell, the header, spacing, aligned tables and matching buttons, and nothing about how they work changes.
11. **Tracing.** One Langfuse session for the improver's work in a round, named `round-<id>`. The claims in a round's evaluations are tagged `run-<id>` and `round-<id>`.
12. **New packages:** `uv add "pyjwt[crypto]"` (it is only in a dependency group now) and `npm install @supabase/supabase-js`. Nothing else.

## The API

Every operation has an `operation_id`, a one-line docstring and no optional inputs. The first group needs a signed-in handler.

| operation_id | Route | Used by |
|---|---|---|
| `start_dispatch`, `start_evaluation`, `sign_off_claim` | existing routes, now signed in | dashboard |
| `start_round`, `stop_round` | `POST /api/rounds`, `POST /api/rounds/{id}/stop` | dashboard (over-limit handlers) |
| `approve_proposal`, `reject_proposal` | `POST /api/rounds/{id}/approve`, `.../reject` | dashboard (over-limit handlers) |
| `update_settings` | `PUT /api/settings` | dashboard (over-limit handlers) |
| `get_me` | `GET /api/me` | dashboard |
| `get_settings` | `GET /api/settings` | dashboard, service |
| `list_configurations`, `list_recipes` | `GET /api/configurations`, `GET /api/recipes` | dashboard |
| `list_rounds`, `get_latest_round` | `GET /api/rounds`, `GET /api/rounds/latest` | dashboard |
| `get_current_configuration`, `get_run_configuration` | `GET /api/configuration/current`, `GET /api/runs/{id}/configuration` | claim agent (no scores) |
| `create_round`, `record_stage`, `record_proposal`, `record_decision`, `record_round_call`, `end_round` | `/api/rounds/...` | service |
| `get_improver_view`, `check_evaluation`, `give_verdict` | `/api/rounds/{id}/...` | improver |

`create_evaluation` gains a nullable `round_id`. `get_evaluation` gains each claim's grade, letter and the two checks, and its measures gain the average grade and the judge's cost.

## Phase 1: Sign-in

Builds the sign-in first, so every later page is built signed in. Also builds the dashboard shell and the shared parts.

- `backend/auth.py` and the handler dependency. `uv add "pyjwt[crypto]"`.
- `handlers` gains `email`. `data/handlers.json` removes the right from Marcus and Priya. Seeding stores the emails.
- The three existing actions need a handler. `sign_off_claim` takes the signed-in handler and no body. `get_me`.
- `npm install @supabase/supabase-js`. `dashboard/src/supabase.ts`, a sign-in page, the token on every request, the header with the handler and Sign out. `envDir` in `vite.config.ts`.
- The shell and the shared parts from decision 10. The sign-off form shows who signs off and, for a handler who can't, says what they can't do and why.
- `e2e/auth.mjs`. Every spec signs in through a helper, and the sign-off spec loses its picker.
- `CODE_STANDARDS.md` rules 7 and the two new ones.

Success criteria:

- [x] `uv run pytest backend` passes, with tests for a valid token; no token, an expired one, the wrong audience and a token signed by another key (all 401); a valid sign-in that is not a handler (403); and each handler action refused without a valid sign-in, one case for each route.
- [x] A test shows a sign-off over the limit refused for Marcus Oyelaran with nothing recorded and the claim still waiting, and accepted for Dana Whitfield with her recorded as the signer on the payment.
- [x] `data/handlers.json` has `false` for Marcus and Priya, and the seeded `handlers` table has all three emails.
- [x] `npm run types` is current, and `npm run check:ts` passes at 80% with tests for the sign-in page, a wrong password's message, the header and Sign out, the token sent on a request, and a 401 returning to sign-in.
- [x] `npm run e2e` passes with every spec signed in. A new spec signs in, signs out, and sees a held payout as Dana (sign-off works) and as Marcus (the page says why he can't, and the backend refuses it).
- [x] Against real Supabase Auth, all three handlers sign in with `HANDLER_PASSWORD`, and the backend accepts each real token (checked with `PyJWKClient` against the project's keys): `/api/me` gives Dana, Marcus and Priya with the right `can_approve_over_limit`, and no token is a 401. The Playwright MCP server is not available in this session, so the browser check of the real sign-in page is done with a Playwright script in Phase 7. Sign-ups are still on in your Supabase project: turn off "Allow new users to sign up" (the lab's setup step 4).
- [x] `npm run check:python` passes.

## Phase 2: The configuration in versions, and Settings

Builds the versioned records, the Configuration and Settings pages, and makes the claim agent run from a version.

- The tables and the seed from decisions 2 and 5. `data/models.json`, `data/improvement.json`, `backend/recipe_improver.md`.
- `backend/configurations.py` (versions, diffs, the current pointer), `backend/settings_store.py` (reads, validated updates, who and when), and the new routes.
- `runs.config_version`. Dispatches and Scoreboard evaluations record the current version, and take the model and effort from it.
- The decisions record the approval limit in force. The instructions fill in the limit from the setting.
- The claim agent fetches its version from `get_run_configuration`, or `get_current_configuration` outside a run, and builds its model from it.
- Dashboard: the Configuration page (the current version, every version with its held-out scores, which are empty until Phase 4, and the diff from the version before) and the Settings page (read for everyone, editable for over-limit handlers, with a confirmation, the models allowed read only, and the appearance switch).

Success criteria:

- [x] `uv run pytest backend agent` passes with tests that:
  - seeding makes version 1 (Luna, `low`, the file's text) and recipe 1, and `npm run seed` takes both back to version 1 after a change;
  - a diff matches one worked out by hand, and a version's diff is against the version before;
  - the run configuration holds no scores (the response's keys are checked);
  - a dispatch and a Scoreboard evaluation each record the current version;
  - a settings change records who and when, is refused for Marcus (403, unchanged) and for values out of range;
  - a new approval limit applies to the next decision, and a decision recorded before it keeps the limit it had;
  - with `MODEL_NAME` set, version 1 uses it at `default`, and the models allowed are that one at a price of 0;
  - the claim agent, on a stub model, is built from the run's version (the model and effort reach `ChatOpenAI`);
  - `agent/main.py` calls no path for evaluations, rounds, settings or scores.
- [x] `npm run check:ts` passes with tests for the Configuration page (versions, scores, a diff with additions and removals) and the Settings page (read only for Marcus with the reason, a change confirmed by the dialog, the theme switch remembered in the browser).
- [x] `npm run e2e` passes: change a setting as Dana and see it on the page after a reload, find it read only as Marcus, switch to dark and see it stay after a reload.
- [x] The same is checked in the browser by the end-to-end specs (`e2e/configuration.spec.ts`): Configuration shows version 1 with Luna at low and its text, Settings shows the starting values, and dark mode holds after a reload. The look in both modes is reviewed in Phase 7, and `npm run seed` on Supabase is done once there.
- [x] `npm run check` passes.

## Phase 3: The judge

Builds grading, and shows it on the Scoreboard and an evaluation's page.

- `backend/judge.py` and `OPENROUTER_SERVER_URL`. `answer_key` gains `reason` and `reference`, seeded from the three files.
- `evaluation_grades`, grading in `end_run` for a finished evaluation, and the new fields on the evaluation's measures and claims.
- Dashboard: the Scoreboard adds the average grade and the judge's cost; an evaluation's page adds each claim's grade, letter and the two checks as yes or no, with "Not graded" where there is none.
- `e2e/judge.mjs`.

Success criteria:

- [ ] `uv run pytest backend` passes, on a stub judge, with tests that:
  - the letter is the most likely level, not the score rounded (probabilities that disagree with the score);
  - an undecided claim gets F with no call to the judge;
  - a claim the judge failed on is "not graded" and left out of the average, and a run with none graded has no grade;
  - 25 calls with a stub that takes 1 second finish in a few seconds, and a call that hangs stops at 10 seconds with retries off;
  - the judge's cost is kept with the evaluation, apart from the claim agent's;
  - the held-out reasons are never in a response about the queue, and `backend/judge.py` is the one place that names the Decisions endpoint;
  - with `MODEL_NAME` set the judge is not called.
- [ ] `npm run check:ts` and `npm run e2e` pass, with the grade column and the checks shown.
- [ ] A live check of the judge on the five `tiny` claims costs under $0.05: I run an evaluation of the five from the Scoreboard on Luna as Dana, and report the grades, the two checks, the judge's cost and the time `end_run` took. It's under 20 seconds.
- [ ] `npm run check` passes.

## Phase 4: The improver

Builds the rounds, the rule, the spending limit and the improver. This is the largest phase, built from the backend outward.

- **Backend:** the tables, `improvement.py` (scores, rule, estimate), the improver's view, the verdict, stopping, and the round routes from the table above. `create_evaluation` takes a `round_id`. `start_claim` refuses past the limit and an evaluation with unstarted copies ends `stopped`.
- **Agent:** `improver.py`, `improver_client.py`, the round's stretches in `service.py` (start, approve, reject, stop, restart), the labelled gate, Langfuse sessions and tags, and the model-call cap.
- `data/README.md`'s "evaluation set" section notes that the reasons are stored.

Success criteria:

- [ ] `uv run pytest backend` passes, with tests that work out the rule by hand:
  - a version kept: accuracy 0.50 to 0.68 (above the 0.1 noise), nothing worse than its tolerance, the objectives met;
  - a version rolled back for each reason: missed an objective, a measure worse than its tolerance, nothing better than its noise, and no grade;
  - cost and time compared as fractions of the old value, accuracy and the grade as differences, with a cost at exactly the tolerance;
  - a version's scores counting only the whole-set evaluations its rounds ran, and ignoring a `tiny` run, a Scoreboard run and a stopped run;
  - the first evaluations a round needs (1 on the evaluation set and 2 on the held-out set), and none when it already has them;
  - the improver's view holding no held-out claim id, summary or reason, only its scores, and holding the five lowest-graded right claims;
  - a round keeping the settings it started with when they change;
  - a stopped round leaving the current version as it was, a round stopped after its verdict keeping it, and the backend refusing anything a stopped round records;
  - an evaluation refused over the limit, one cut short showing as `stopped`, a version whose evaluation was stopped rolled back, and the estimate for a model with no runs scaled by the larger price ratio;
  - one open round at a time, and a version recorded with its proposer, approver and time.
- [ ] `uv run pytest agent` passes, with the stub model and an in-memory checkpointer, with tests that:
  - a proposal changes nothing until it is approved, then makes a version with the handler's name and the round;
  - a rejection's reason reaches the improver, which may propose again;
  - a proposal outside the models allowed goes back to the improver and never reaches a handler (the round's pending proposal stays empty);
  - an approval while a dispatch runs is refused, naming it, with nothing recorded;
  - the improver's evaluations are not refused for the round's own stretch, and a stopped round starts no more claims or model calls;
  - a round waiting for a handler lets a dispatch run, and survives a service restart (a new service on the same checkpointer resumes it);
  - a service start marks a mid-work round stopped and leaves a waiting one open;
  - the improver's client has no way to the claim agent's routes, and the claim agent's client has none to the improver's;
  - the improver runs with parallel tool calls off, its model calls are capped, and its Langfuse session is `round-<id>`.
- [ ] `npm run check:python` passes with 80% coverage.
- [ ] I run a round on the stub model against a throwaway SQLite backend and the in-memory checkpointer with `uv run`, and report the stages it went through.

## Phase 5: The Improvement page

Builds the page a handler runs the improver from.

- **Improvement:** a header that says what the page is for, Start a round (confirmed, over-limit handlers) and Stop (confirmed), where the round has got to as it happens (the seven stages with 1, 2 or 3 of 3 where they have a count, refreshed every 2 seconds, nothing jumping), the proposal waiting with its diff, the model and effort before and after, the reason, Approve and Reject (a reason in the dialog), the rounds so far newest first with the version tried, the improver's reason, held-out scores before and after, the verdict and why, who approved or rejected what and when, and the cost, the chart, and the recipe's versions.
- The diff component for versions and recipes. For a handler who can't, the page says what they can't do and why, in place of the buttons.
- The Configuration page now shows each version's held-out scores.
- `e2e/agent.mjs` gains the round calls, and the end-to-end test plays a round out through the API as the service would.

Success criteria:

- [ ] `npm run check:ts` passes at 80% with tests for each stage's label and count, the proposal with its diff and the model before and after, Approve and Reject confirmed with a reason, Marcus seeing the reason he can't act, the empty state before any round, the error state, the rounds table with right-aligned numbers, the chart's marks, and polling that holds still when nothing changed.
- [ ] `npm run e2e` passes: sign in as Dana, start a round, play the service's calls through the API to a waiting proposal, see the diff, approve it in the dialog, and see the stand-in receive the approval with the handler. A second test rejects with a reason. A third, as Marcus, sees the explanation.
- [ ] In the browser against `npm run dev`, the page renders with seeded rounds written through the API, in light and dark mode, with no horizontal scroll at 1280 px.
- [ ] `npm run check` passes.

## Phase 6: The look of the whole dashboard

Brings the existing pages up to the same standard, with no change to how they work.

- Claims, Scoreboard and the claim's page get the shell's header, the same spacing, tables that line up with numbers right-aligned and units, buttons that look alike, statuses as a color and a word, and loading, empty and error states.
- A review of every page against `docs/BRAND.md` in both modes: contrast, the avoided borders, fonts, money as $1,250.00.

Success criteria:

- [ ] `npm run check:ts` and `npm run e2e` pass with the existing specs unchanged apart from signing in, which shows that nothing on the existing pages works differently.
- [ ] Screenshots of every page (sign-in, Claims with a claim open, Scoreboard and an evaluation, Improvement, Configuration, Settings) in light and dark mode at 1280 px, taken with Playwright MCP, show the brand's backgrounds, fonts and colors, a page header, aligned tables and no jumping on refresh.
- [ ] A grep of `dashboard/src` for `border-top`, `border-left` and `border-right` finds no one-sided border on a panel, and `any` is still absent.

## Phase 7: Live runs and the documents

Does the live checks the brief lists, finishes the documents and does the final check. This uses your OpenRouter key, and I keep to the runs below. Please check before this phase that the key has a credit limit, as the lab's setup says.

- Reseed once with `npm run seed`, and keep to these runs.
- **Two rounds as Dana Whitfield from the Improvement page,** on Luna. Reject the first proposal with a reason, then approve the next one, and approve the rest. Check each round on the page as it happens, and afterwards the scores before and after, the verdict and the cost. Check the Configuration page's versions and diffs. Through Langfuse's observations API, find each round's improver traces in the one session `round-<id>`. Report how far version 1's two held-out runs differed on each measure, beside the noise and tolerance.
- **A dispatch at N=25 on the current version.** As Marcus Oyelaran, a payout waiting for sign-off can't be signed off and the settings can't be changed. As Dana, the same payout is signed off and paid.
- **Screenshots of every page in light and dark mode.** A fresh subagent reviews them once, as a senior product designer would, against `docs/BRAND.md` and point 8 of the brief, and I fix what it finds.
- **The documents:** `docs/SPEC.md` (the approval limit is a setting, the improver reads the handlers' decisions and reasons on the evaluation set while the claim agent never sees them and nothing in the agent reads the held-out set's, sign-in, the improver, and what the team needs to see), `docs/TECH_STACK.md`, `docs/PROCESS.md`, `docs/CODE_STANDARDS.md`, `docs/STATUS.md` (including what the live runs cost, how to set up the sign-ins and a note to reseed after changing `MODEL_NAME`) and `data/README.md`.

Success criteria:

- [ ] Two rounds ran as above. I report each round's version, reason, held-out scores before and after, verdict and cost, and the order of the stages the page showed.
- [ ] Each round's improver traces are in one Langfuse session, and a round's evaluation claims are found by the tag `round-<id>`.
- [ ] The dispatch at N=25 finished. Marcus was refused on the sign-off and on a setting, with the reason on the page. Dana's sign-off paid the claim.
- [ ] The designer's review is done once and what it found is fixed. I list each finding.
- [ ] I report what the live checks spent. On Luna it comes to under $1.
- [ ] The documents describe the product as it now is.
- [ ] `npm run check` passes with `.env` moved out of the way, and `git status` is clean. Every phase is committed with the hook run, and nothing is pushed.

## Risks I'll watch

- **Time per claim.** The lab's 25% tolerance on time was measured without a database. Claims took 12.4 seconds at N=3 and 21.4 at N=25 in the swarm build. Every evaluation in a round runs at the same agents-at-once setting, so the comparison holds, but I'll report the spread in the live check.
- **Luna ends its turn without a decision on about one claim in fourteen.** Those claims are graded F, which pulls the average grade down for every version.
- **The order of the two `after_model` hooks** is the part I haven't proved. Phase 4's first test settles it.
- **A round costs real money and takes minutes,** so the unit tests use stubs everywhere, and the live checks keep to the two rounds above.
- **A student on a free model** can build and test everything but not run a round, as the brief says.

## Out of scope

As the brief says: the improver changing the objectives, noise, tolerance, spending limit, models allowed, approval limit, evaluation sets or the judge. Also not built: sign-in for reads or the agent's calls (Week 3), a sign-up page, changing the models list on the page, and any change to the claim agent's graph or tools.

## Questions for you

- **CODE_STANDARDS rules.** The brief names rules 7, 13 and 14, and the file has 8. I'll update rule 7 and add two (the Supabase client in one module, and instructions written as goals). Is that what you want, or do you have the longer file?
- **Reads stay open.** Only actions check the signed-in handler, as the brief says. The agent and the service need open reads until Week 3 anyway. OK?
- **`MODEL_NAME`.** I apply it when seeding and to the models allowed, with no override at run time, so you reseed after changing it. The other choice is to override the version's model on every call, which means the Configuration page can show a model the agent isn't using. I prefer the first.
- **The estimate leaves out the judge,** as the brief words it, while the round's spend counts it. A round could overshoot the limit by about the judge's cost for one evaluation, a fraction of a cent. Fine?
