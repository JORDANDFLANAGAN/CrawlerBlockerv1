# Tar Pit v1 — Tasks

**Status:** Draft · **Date:** 2026-10-21 · **Design:** [design.md](./design.md)

## Legend
- `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked
- `parallel: <group>` — tasks sharing a group id can run concurrently
- `depends_on: <ids>` — must complete before this task starts

---

## T1 — Rename scaffold: `crawlerblocker` → `tarpit`

- **Owns:**
  - `pyproject.toml` (modify)
  - `.env.example` (modify)
  - `CLAUDE.md` (modify — paths and env prefix only)
  - `src/crawlerblocker/` (delete entire tree)
  - `tests/test_health.py` (delete)
  - `src/tarpit/__init__.py` (create, empty)
  - `src/tarpit/routers/__init__.py` (create, empty)
- **Depends on:** —
- **Parallel group:** W1 (serial — hard blocker for everything else)
- **Acceptance:**
  - [~] `src/crawlerblocker/` no longer exists; `src/tarpit/` exists with empty `__init__.py` and `routers/__init__.py`
  - [ ] `pyproject.toml`: `[project.name] = "tarpit"`; `[project.scripts]` entry is `tarpit = "tarpit:main"`
  - [ ] `.env.example` keys renamed from `CRAWLERBLOCKER_*` to `TARPIT_*` with the full set from [design.md §2.2 settings](./design.md#srctarpitsettingspy) (`TARPIT_HOST`, `TARPIT_PORT`, `TARPIT_RELOAD`, `TARPIT_LOG_LEVEL`, `TARPIT_CORPUS_PATH`, `TARPIT_LOG_PATH`, `TARPIT_DELAY_MIN_S`, `TARPIT_DELAY_MAX_S`)
  - [ ] `.env.example` default for `TARPIT_RELOAD` is `false` (design decision — reload is opt-in)
  - [ ] `CLAUDE.md` has no remaining references to `crawlerblocker` or `CRAWLERBLOCKER_`; paths updated to `src/tarpit/` and env prefix to `TARPIT_`
  - [ ] `tests/test_health.py` deleted; `tests/__init__.py` retained
  - [ ] `uv run python -c "import tarpit"` succeeds (empty package import works)
  - [ ] `uv run pytest -q` runs with zero tests collected, exit 0 (no stale tests)
- **Notes:** Pure plumbing. No new code logic lands in this task. Do **not** stub out any module files (`markov.py`, `ids.py`, etc.) — those are owned by later tasks and stub files here would collide. Also do **not** delete the `[dependency-groups] dev` block in `pyproject.toml`.

---

## T2 — `ids.py` — ID validation and `{new_id}` generation

- **Owns:**
  - `src/tarpit/ids.py` (create)
  - `tests/test_ids.py` (create)
- **Depends on:** T1
- **Parallel group:** W2a
- **Acceptance:**
  - [ ] Public surface matches [design.md §2.2 `ids.py`](./design.md#srctarpitidspy) exactly: `ID_PATTERN`, `NEW_ID_PATTERN`, `BAIT_ID`, `generate_new_ids`, `is_valid_id`
  - [ ] `generate_new_ids(random.Random("x"), 10)` returns the **same** list on every call — determinism test
  - [ ] `generate_new_ids` outputs match `NEW_ID_PATTERN` for every element (4-char lowercase hex) — property-style check over 100 random seeds
  - [ ] `is_valid_id` returns True for `"root"`, `"a8f3"`, 64-char all-dot `"."*64`, mixed-case alphanum
  - [ ] `is_valid_id` returns False for `""`, 65-char input, strings containing `/`, `<`, space, `%20`
  - [ ] `BAIT_ID == "root"` and `is_valid_id(BAIT_ID)` is True — covers REQ-BAIT-03
- **Notes:** Satisfies REQ-MAZE-01, REQ-MAZE-07 (validation side), and the generation half of REQ-MAZE-03. Tests use seeded `random.Random` directly — no mocks.

---

## T3 — `markov.py` — order-2 Markov chain

- **Owns:**
  - `src/tarpit/markov.py` (create)
  - `tests/test_markov.py` (create)
- **Depends on:** T1
- **Parallel group:** W2a
- **Acceptance:**
  - [ ] Public surface matches [design.md §2.2 `markov.py`](./design.md#srctarpitmarkovpy) exactly: `class MarkovChain` with `from_corpus_text` classmethod and `generate` method
  - [ ] Order is 2 (window of 2 words). Verified by constructing a chain from a known tiny corpus and asserting internal `followers` keys are 2-tuples
  - [ ] Determinism: `chain.generate(random.Random(42), 50)` returns the same list on every call
  - [ ] Reproducibility: `MarkovChain.from_corpus_text(same_text).generate(random.Random(42), 50)` returns the same list across chain rebuilds
  - [ ] Mid-walk dead-end handling: craft a corpus where the walk hits a pair with no followers; `generate` SHALL re-draw a sentence-start pair and never raise — REQ-MAZE-05 behavior
  - [ ] Sentence-start selection respects `.`, `!`, `?` as sentence terminators
  - [ ] Tests use a toy inline corpus (10–30 words); do **not** depend on `corpus.txt`
- **Notes:** Satisfies REQ-GEN-01 (training side), REQ-GEN-02 (generation side), REQ-GEN-03 (seeded start). Pure in-memory; no I/O.

---

## T4 — `renderer.py` — pure HTML composition

- **Owns:**
  - `src/tarpit/renderer.py` (create)
  - `tests/test_renderer.py` (create)
- **Depends on:** T1
- **Parallel group:** W2a
- **Acceptance:**
  - [ ] Public surface matches [design.md §2.2 `renderer.py`](./design.md#srctarpitrendererpy) exactly: `render_robots_txt`, `render_root_page`, `render_maze_page`
  - [ ] `render_robots_txt()` returns the exact string `"User-agent: *\nDisallow: /maze/\n"` — REQ-ROBOTS-01
  - [ ] `render_root_page()` output contains the human-copy sentence from [design.md §2.1 `/`](./design.md#get-) verbatim — REQ-BAIT-04
  - [ ] `render_root_page()` output contains exactly one `<a style="display:none" href="/maze/root">` — REQ-BAIT-01, REQ-BAIT-02, REQ-BAIT-03
  - [ ] `render_maze_page("a8f3", ["ahab", "whale"], ["1111","2222"])` output: HTML-escapes every inserted value; contains one `<p>` with the joined words; contains exactly 2 anchors with `style="display:none"` and `href="/maze/1111"`, `href="/maze/2222"` — REQ-MAZE-03
  - [ ] XSS check: `render_maze_page("<x>", ["<script>"], ["<y>"])` output has no raw `<script>` substring; `<` characters appear only as `&lt;` — REQ-GEN / REQ-SEC-01
  - [ ] No module-level I/O, no `random`, no `datetime` — functions are pure
- **Notes:** Satisfies REQ-ROBOTS-01, REQ-BAIT-01, REQ-BAIT-02, REQ-BAIT-04, REQ-MAZE-03 shape, REQ-SEC-01. Use `html.escape(..., quote=True)` on every inserted value.

---

## T5 — `access_log.py` — JSON-lines access-log writer

- **Owns:**
  - `src/tarpit/access_log.py` (create)
  - `tests/test_access_log.py` (create)
- **Depends on:** T1
- **Parallel group:** W2b
- **Acceptance:**
  - [ ] Public surface matches [design.md §2.2 `access_log.py`](./design.md#srctarpitaccess_logpy) exactly: `@dataclass(frozen=True) AccessRecord`, `class AccessLogWriter` with `__init__(file, lock)` and `async append(record)`
  - [ ] Written JSON line matches [design.md §2.4 schema](./design.md#24-access-log-json-schema): key order `ts, ip, ua, path, delay_s`; one line, newline-terminated
  - [ ] `ua=None` serialises as JSON `null`
  - [ ] `delay_s` written to 2 decimal places (e.g. `3.4` or `3.42`, never `3.4000000000001`)
  - [ ] `append` runs the blocking `write + flush` via `asyncio.to_thread`
  - [ ] Concurrent-append test: fire 20 `append` calls on the same writer with `asyncio.gather`; file has exactly 20 lines, every line is parseable JSON, no interleaved bytes
  - [ ] `AccessRecord` is frozen (mutation attempt raises `FrozenInstanceError`)
- **Notes:** Satisfies REQ-LOG-01 (schema + append-per-request), REQ-LOG-03 (shape forbids extra fields by construction). Tests write to a `tmp_path` file via pytest fixture.

---

## T6 — `settings.py` — pydantic-settings config

- **Owns:**
  - `src/tarpit/settings.py` (create)
- **Depends on:** T1
- **Parallel group:** W2b
- **Acceptance:**
  - [ ] Public surface matches [design.md §2.2 `settings.py`](./design.md#srctarpitsettingspy) exactly: `class Settings`, `get_settings()` wrapped in `functools.lru_cache`
  - [ ] All field names, types, and defaults match the design table exactly
  - [ ] `reload` default is `False`
  - [ ] `env_prefix` is `"TARPIT_"` and `env_file` is `".env"`
  - [ ] Smoke import: `uv run python -c "from tarpit.settings import get_settings; print(get_settings().port)"` prints `8000`
- **Notes:** No separate test file — pydantic-settings covers its own type coercion; smoke import is enough. If you add behavior beyond field declarations, add tests.

---

## T7 — `corpus.txt` — Moby-Dick plain text

- **Owns:**
  - `src/tarpit/corpus.txt` (create)
- **Depends on:** T1
- **Parallel group:** W2b
- **Acceptance:**
  - [ ] File contains the body text of *Moby-Dick* by Herman Melville (public domain). Source: Project Gutenberg (gutenberg.org/ebooks/2701) plain text download
  - [ ] Project Gutenberg header (before "*** START OF THE PROJECT GUTENBERG EBOOK …") and footer (from "*** END OF THE PROJECT GUTENBERG EBOOK …") are stripped
  - [ ] Encoding: UTF-8
  - [ ] File size roughly 1.0 – 1.3 MB (sanity check — not an exact byte count)
  - [ ] `uv run python -c "from pathlib import Path; t = Path('src/tarpit/corpus.txt').read_text(); assert 'Call me Ishmael' in t and 'END OF THE PROJECT GUTENBERG' not in t"` exits 0
- **Notes:** No code. Data drop only. Do **not** commit any Gutenberg boilerplate — that text is licensed separately and we don't want it in our repo.

---

## T8 — `README.md` — install / run / verify instructions

- **Owns:**
  - `README.md` (replace — currently empty)
- **Depends on:** T1
- **Parallel group:** W2c
- **Acceptance:**
  - [ ] Documents install with `uv sync`
  - [ ] Documents run with `uv run tarpit` (single uvicorn worker by default — explicit warning not to raise `--workers` in v1)
  - [ ] Documents the three endpoints: `GET /robots.txt`, `GET /`, `GET /maze/{id}`
  - [ ] Documents where `access.log` is written (`logs/access.log`, relative to CWD) and that the `logs/` directory is auto-created at startup
  - [ ] Includes a "Verify v1" section with one shell snippet per Done-when item from [source spec](../../.claude/specs/TarPit.md): robots disallow, curl loop never dead-ends, same URL = identical body, visible 2–5s delay, access log is JSON lines
  - [ ] Documents `.env` override pattern (copy `.env.example` → `.env`; `TARPIT_*` vars)
- **Notes:** Satisfies REQ-DOC-01. Keep it tight — one page. No marketing fluff.

---

## T9 — `dependencies.py` — FastAPI dependency providers

- **Owns:**
  - `src/tarpit/dependencies.py` (create)
- **Depends on:** T3, T5, T6
- **Parallel group:** W3 (serial — single task in wave)
- **Acceptance:**
  - [ ] Public surface matches [design.md §2.3](./design.md#23-fastapi-dependency-contract) exactly: `get_chain(request)`, `get_log_writer(request)`, `get_settings_dep()`
  - [ ] `get_chain` returns `request.app.state.chain` (type-annotated as `MarkovChain`)
  - [ ] `get_log_writer` returns `request.app.state.log_writer` (type-annotated as `AccessLogWriter`)
  - [ ] `get_settings_dep` delegates to `tarpit.settings.get_settings`
  - [ ] No test file needed — behavior is a one-liner per provider; coverage comes via the router tests in T10/T11
- **Notes:** This module is the single seam between the lifespan-managed singletons and the handlers. Changing its contract is a design re-open.

---

## T10 — Routers: `/` and `/robots.txt` + their tests

- **Owns:**
  - `src/tarpit/routers/root.py` (create)
  - `tests/test_root.py` (create)
  - `tests/test_robots.py` (create)
- **Depends on:** T4, T9
- **Parallel group:** W4
- **Acceptance:**
  - [ ] `router: APIRouter` exports exactly two routes: `GET /` and `GET /robots.txt`
  - [ ] `GET /robots.txt`: status 200, `Content-Type: text/plain; charset=utf-8`, body exactly `"User-agent: *\nDisallow: /maze/\n"` — REQ-ROBOTS-01
  - [ ] `GET /`: status 200, `Content-Type: text/html; charset=utf-8`, body contains the human-copy sentence and the hidden `/maze/root` anchor — REQ-BAIT-01..04
  - [ ] Neither handler writes an access log line (verified by checking that an injected mock log writer records zero calls)
  - [ ] Tests construct a minimal `FastAPI()` app in-file and include the router — they do **not** import `tarpit.main.app` (which doesn't exist until T12)
  - [ ] Tests use `fastapi.testclient.TestClient`
- **Notes:** Satisfies REQ-ROBOTS-01, REQ-BAIT-01, REQ-BAIT-02, REQ-BAIT-03, REQ-BAIT-04. Handlers are thin — they call `render_*` and return the result.

---

## T11 — Router: `/maze/{id}` + its test

- **Owns:**
  - `src/tarpit/routers/maze.py` (create)
  - `tests/test_maze.py` (create)
- **Depends on:** T2, T3, T4, T5, T9
- **Parallel group:** W4
- **Acceptance:**
  - [ ] `router: APIRouter` exports `GET /maze/{id}` with path param constrained via `Path(..., pattern=r"^[A-Za-z0-9._~-]{1,64}$")` — REQ-MAZE-01, REQ-MAZE-07
  - [ ] Handler constructs `rng = random.Random(id)`, then: (a) calls `chain.generate(rng, 200)` where 200 is a module-level constant `WORDS_PER_PAGE`, (b) picks `k = rng.randint(5, 10)`, (c) calls `generate_new_ids(rng, k)`, (d) renders via `render_maze_page`, (e) awaits `asyncio.sleep(random.uniform(settings.delay_min_s, settings.delay_max_s))` (module `random`, not the per-request `rng`), (f) appends an `AccessRecord` via the log writer, (g) returns `HTMLResponse` — REQ-MAZE-02..06, REQ-DELAY-01..02, REQ-GEN-02, REQ-LOG-01
  - [ ] **Determinism test**: with `TARPIT_DELAY_MIN_S=0 TARPIT_DELAY_MAX_S=0`, two calls to `GET /maze/a8f3` return byte-identical response bodies — REQ-MAZE-02
  - [ ] **Link count test**: parse the response HTML; the number of `/maze/...` anchors is between 5 and 10 inclusive — REQ-MAZE-03
  - [ ] **Dead-end test**: every anchor's `href` matches `^/maze/[0-9a-f]{4}$` — REQ-MAZE-03/REQ-MAZE-05 combined
  - [ ] **Invalid-id tests**: `GET /maze/`, `GET /maze/<script>`, `GET /maze/` + 65×`a` → each returns 404, no response body generation, no access log line written (verified via injected mock writer) — REQ-MAZE-07
  - [ ] **Delay test**: with `TARPIT_DELAY_MIN_S=0.1 TARPIT_DELAY_MAX_S=0.1`, measure wall-clock between request start and response; assert elapsed ≥ 0.1s — REQ-DELAY-01
  - [ ] **Log-field test**: inspect the written log record — fields exactly `ts`, `ip`, `ua`, `path`, `delay_s` in that order; `path == "/maze/a8f3"`; no query string even if one is supplied in the URL — REQ-LOG-01, REQ-LOG-03
  - [ ] **500-path test**: inject a `MarkovChain` whose `generate` raises; response is 500, `Content-Type: text/plain`, body exactly `"internal error"`, **no** access log line written — [design.md §2.1 error path](./design.md#getmazeid)
  - [ ] Tests construct a minimal FastAPI app in-file with the router included and lifespan singletons overridden via `app.state`
- **Notes:** The single largest task. If this feels too big during execution, acceptable split points: (a) handler + its tests, (b) error-path tests. But keep the file ownership as declared.

---

## T12 — Wire-up: `main.py`, `__init__.py`, lifespan, end-to-end smoke

- **Owns:**
  - `src/tarpit/main.py` (create)
  - `src/tarpit/__init__.py` (modify — currently empty from T1)
  - `tests/test_e2e_smoke.py` (create)
- **Depends on:** T10, T11
- **Parallel group:** W5 (serial — single task in wave)
- **Acceptance:**
  - [ ] `src/tarpit/__init__.py` exports `app` and `main` only
  - [ ] `main.py` implements `create_app() -> FastAPI` with the lifespan from [design.md §1](./design.md#1-architecture-overview) + §3 lifecycle:
    - reads corpus from `settings.corpus_path`, builds `MarkovChain`, stores on `app.state.chain`
    - creates `settings.log_path.parent` if missing; opens the log file in append mode; constructs `AccessLogWriter` with an `asyncio.Lock`; stores on `app.state.log_writer`
    - closes the log file in the shutdown half of the lifespan
    - includes `root.router` and `maze.router`
  - [ ] `main()` reads `settings`, calls `uvicorn.run("tarpit:app", host=..., port=..., reload=..., log_level=...)`
  - [ ] `main()` passes `workers=1` explicitly (or does not set `workers` — the default is 1); if the implementor plans to accept a workers setting, do **not** add one in v1
  - [ ] **Startup test**: `tests/test_e2e_smoke.py` boots the real app in-process via `uvicorn.Server` on an ephemeral port, hits all three endpoints, asserts status codes and basic shape — including that `logs/access.log` gains one line after a `/maze/{id}` request. Tests force `TARPIT_DELAY_MIN_S=0 TARPIT_DELAY_MAX_S=0`
  - [ ] **Corpus-missing test**: starting the lifespan with `settings.corpus_path` pointing to a non-existent file raises at startup (fail-fast — [design.md §6 risks](./design.md#6-risks--edge-cases))
  - [ ] After this task, `uv run tarpit` starts the service and `uv run pytest -q` is all green
- **Notes:** Final integration. If the e2e smoke test flakes on timing, bump the sleep tolerance — do **not** weaken determinism checks.

---

## Execution plan

Agent cap: **3 concurrent implementors per wave**. Wave 2 is therefore split into three subwaves below.

- **Wave 1** (serial): **T1** — scaffold rename
- **Wave 2a** (parallel group W2a, 3 agents): **T2** ids, **T3** markov, **T4** renderer — core deterministic logic, reviewed first so any failure here cascades out early
- **Wave 2b** (parallel group W2b, 3 agents): **T5** access_log, **T6** settings, **T7** corpus — supporting infra and data
- **Wave 2c** (parallel group W2c, 1 agent): **T8** README
- **Wave 3** (serial): **T9** — dependencies
- **Wave 4** (parallel group W4, 2 agents): **T10, T11**
- **Wave 5** (serial): **T12** — wire-up + e2e

Max fan-out: **3 agents** (in W2a, W2b, and under the overall cap for W4).

## Risks for execution

- **T1 is a hard blocker.** If T1 leaves pyproject or imports in a broken state, every W2 task will fail its smoke import. Reviewer must verify T1 by running `uv sync && uv run python -c "import tarpit"` before W2 dispatches.
- **T3 Markov determinism is Python-version-sensitive.** `random.Random(str)` seeding is stable in Python ≥ 3.11, but the walk's choice of start pair depends on dict/set ordering if iteration order leaks in. Implementors must iterate `followers` keys through a sorted list (or an insertion-ordered list captured at build time) before any `rng.choice` call. If this isn't done, T11's determinism test will fail even though T3's own tests pass.
- **T7's corpus fetch is implementor-side side-effectful.** The agent will likely `curl` from gutenberg.org. If the exact URL or header-strip markers change, the file content shifts. Lock to the plain-text UTF-8 edition at `https://www.gutenberg.org/cache/epub/2701/pg2701.txt` and strip between the explicit `*** START ...` and `*** END ...` markers.
- **T11's `/maze/{id}` is the largest surface.** If the reviewer PASSes it with weak assertions, downstream bugs surface in T12's e2e smoke. Reviewer should run `uv run pytest -q tests/test_maze.py` with `TARPIT_DELAY_MIN_S=0 TARPIT_DELAY_MAX_S=0` and verify every acceptance item individually.
- **Multi-worker footgun.** If anyone later adds a `workers` setting or passes `--workers N` to uvicorn, the single-file access log will race. T12 and T8 (README) both call this out; coordinator should treat any PR adding a workers knob as a design re-open.
- **`src/tarpit/__init__.py` is co-owned across T1 (create empty) and T12 (populate).** This is safe because the waves are serial between them, but if someone reruns T1 after T12, it will blank the exports. Coordinator must not re-run T1 after W5 starts.
