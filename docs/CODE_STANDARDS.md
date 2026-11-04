# Code standards

1. Keep it simple, do not overcomplicate.
2. Comments only when necessary.
3. Be concise: a short README and no emojis.
4. IMPORTANT: avoid overly defensive code. Handle exceptions only when necessary.
5. Use uv: ALWAYS `uv run`, NEVER `python3`.
6. TypeScript: strict mode, never `any`.
7. The agent has four parts: the claim agent (`main.py`), the dispatcher (`dispatcher.py`), the agent's service (`service.py`) and the command line (`__main__.py`), sharing `settings.py` and `tracing.py`. The dispatcher touches the claim agent only through `main.work_claim`.
8. Every process holds at most 4 database connections, which the claim agents and the backend's requests wait for rather than fail. A route takes its connection and returns it in its own thread (`with_db`), never in a dependency, so a thread that waits holds no connection and one that holds a connection can always finish.
