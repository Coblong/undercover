# Code standards

1. Keep it simple, do not overcomplicate.
2. Comments only when necessary.
3. Be concise: a short README and no emojis.
4. IMPORTANT: avoid overly defensive code. Handle exceptions only when necessary.
5. Use uv: ALWAYS `uv run`, NEVER `python3`.
6. TypeScript: strict mode, never `any`.
7. The agent has six parts: the claim agent (`main.py`), the dispatcher (`dispatcher.py`), the agent's service (`service.py`), the command line (`__main__.py`), the improver (`improver.py`) and the improver's client for the backend (`improver_client.py`), sharing `settings.py` and `tracing.py`. The dispatcher touches the claim agent only through `main.work_claim`. The claim agent's client has no way to the scores, the handlers' decisions or the improver's routes, and the improver's client has none to the claim agent's.
8. Every process holds at most 4 database connections, which the claim agents and the backend's requests wait for rather than fail. A route takes its connection and returns it in its own thread (`with_db`), never in a dependency, so a thread that waits holds no connection and one that holds a connection can always finish.
9. The dashboard has one Supabase client, in `dashboard/src/supabase.ts`. Nothing else imports `@supabase/supabase-js`, and the dashboard sends the session's access token with each request.
10. Instructions for an agent are written as goals, not as a script of steps: what the agent works from, what it is for and what it must never do. The improver's recipe is data the improver follows, so it may be a list of steps.
