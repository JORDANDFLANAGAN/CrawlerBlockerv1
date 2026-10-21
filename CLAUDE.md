# CrawlerBlocker

FastAPI service. Python 3.13 managed by `uv`.

## Tooling

- Use `uv` for everything. Never `pip`, never a hand-rolled `venv`.
  - Add runtime dep: `uv add <pkg>`
  - Add dev dep: `uv add --dev <pkg>`
  - Run commands: `uv run <cmd>`
- Run the app: `uv run crawlerblocker` (or `uv run uvicorn crawlerblocker:app --reload`).
- Run tests: `uv run pytest -q`.

## Layout

- Source: `src/crawlerblocker/`
- Routers: `src/crawlerblocker/routers/<resource>.py` — one file per resource, exports `router = APIRouter(...)`.
- Settings: `src/crawlerblocker/settings.py` — pydantic-settings, env prefix `CRAWLERBLOCKER_`.
- Tests: `tests/test_<thing>.py`, mirroring the source tree.

## Code standards

- Simplest readable solution wins. Don't abstract until you have three concrete uses — two is a coincidence.
- No speculative features, no "might need later" hooks, no backwards-compat shims for code that has no callers yet.
- Type-annotate every public function signature. Prefer built-in generics (`list[X]`, `dict[str, X]`) over `typing.List` / `Dict`.
- Validate at system boundaries (HTTP input, external APIs). Trust internal code — don't add defensive checks for conditions the type system already rules out.
- No `try/except` without a specific exception type and a reason the catch exists. Never bare `except:`.
- Prefer small modules over god-files, but don't split a 40-line file into three just because.

## Testing standards (strict)

- **Tests describe behavior, not implementation.** A test asserts "given X, the system does Y." It should not know what internal functions were called, what order, or how state is laid out.
- **Never alter a test to make it pass.** If a test fails, the implementation is wrong — fix the implementation. The only legitimate reason to edit a test is when the *behavior being specified* has intentionally changed; in that case, the test change and the implementation change land together and the commit message says what behavior shifted and why.
- Test file changes without a corresponding, intentional behavior change are a code smell. Pushback is expected.
- One assertion-concept per test. A test named `test_health_ok` asserts the health behavior; it does not also check logging format.
- Prefer the real thing over mocks. Mock only at process boundaries (network, filesystem, time, randomness) and only when the real thing is slow or non-deterministic.
- Every new route gets a test before it's considered done.

## Naming

- Clarity over brevity. `blocked_crawler_ids` beats `bci`. `get_crawler_by_fingerprint` beats `get_cbf`.
- Functions are verbs (`parse_user_agent`), variables are nouns (`user_agent`), booleans read as predicates (`is_blocked`, `has_fingerprint`).
- Modules: lowercase, no underscores unless truly needed for readability. Routers named after the resource (`crawlers.py`, not `crawler_router.py`).
- No Hungarian notation, no type prefixes, no `_impl` suffixes.

## Comments

- Default: write none. Well-named code is self-documenting.
- Write a comment only when the **why** is genuinely non-obvious: a hidden constraint, a workaround for a specific bug, a subtle invariant, behavior that would surprise a careful reader.
- Never explain *what* the code does — the code already says that.
- Never reference tickets, PRs, authors, or dates in comments. That context belongs in commit messages and PR descriptions, and rots in source.

## Security & secrets

- **Nothing sensitive goes in source.** No API keys, no passwords, no tokens, no customer data, no internal URLs. Config comes from env vars (loaded via `settings.py`); secrets come from env vars or a secrets manager at runtime.
- `.env` is gitignored. `.env.example` is the only env file in the repo and contains **placeholder values only** — never real credentials, never real hostnames.
- If a secret ever lands in a commit (even locally, pre-push), treat it as compromised: rotate the credential, then purge from history. Don't just delete the file — `git log -p` still has it.
- When reading config, use `pydantic.SecretStr` for anything sensitive so it masks in logs and reprs.
- Never log request bodies, headers, or query strings unconditionally. Log IDs and shapes, not contents.

## Attack surface

- **Validate every external input.** Use pydantic models for request bodies and query params — don't accept raw `dict` / `Any`. Trust nothing that crosses the HTTP boundary.
- **No raw SQL from user input.** When a DB lands, use a parameterised query layer (SQLAlchemy, databases, etc.). String-concatenated SQL is a bug.
- **No shell / subprocess calls with user input.** If subprocess is unavoidable, pass args as a list, never `shell=True`.
- **CORS is deny-by-default.** Only add `CORSMiddleware` with an explicit allow-list of origins. Never `allow_origins=["*"]` with `allow_credentials=True`.
- **Auth is not optional for non-public routes.** New routers either carry an auth dependency or are explicitly documented as public (e.g. `/health`).
- **Rate-limit anything that touches an upstream or does work proportional to input size.**
- **Dev-only flags stay off by default.** `uvicorn --reload`, `debug=True`, and FastAPI's `/docs` + `/redoc` are useful locally but must be gated by settings before any non-local deployment.
- **Dependencies drift into vulnerabilities.** Pin via `uv.lock` (already done), review `uv add` output for yanked / unknown packages, and run `uv lock --upgrade` on a cadence — never auto-accept a transitive bump without reading the diff.
- **Don't echo errors verbatim to clients.** Return a stable error shape; log the detail server-side. Stack traces in responses leak internals.

## Framework quirks

- FastAPI 0.156+ wraps included routers in an internal `_IncludedRouter` object. `app.routes` won't list router paths directly — verify endpoints by calling them (TestClient or `uv run pytest`), not by introspecting `app.routes`.
