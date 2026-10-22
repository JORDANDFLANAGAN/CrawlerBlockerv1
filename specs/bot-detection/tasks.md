# Bot Detection (v2.1) — Tasks

**Status:** Draft · **Date:** 2026-10-21 · **Design:** [design.md](./design.md)

## Legend
- `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked
- `parallel: <group>` — tasks sharing a group id can run concurrently
- `depends_on: <ids>` — must complete before this task starts

---

## T1 — Scaffold: `detection/` package, `settings.py` fields, `.env.example` entries

- **Owns:**
  - `src/tarpit/detection/__init__.py` (create, empty)
  - `src/tarpit/settings.py` (modify — add five fields)
  - `.env.example` (modify — add five env vars)
- **Depends on:** —
- **Parallel group:** W1 (serial — hard blocker for everything else)
- **Acceptance:**
  - [x] `src/tarpit/detection/__init__.py` exists and is empty (no stubs for state/log/signals/middleware — those are owned by later tasks)
  - [x] `src/tarpit/settings.py` gains exactly five new fields with the names, types, and defaults from [design.md §2.2 settings.py additions](./design.md#srctarpitsettingspy--additions): `detection_enabled: bool = True`, `detection_log_path: Path = Path("logs/detection.log")`, `detection_rate_budget: int = 60`, `detection_rate_window_s: float = 60.0`, `detection_state_max_entries: int = 10_000`
  - [x] All v1 `Settings` fields and defaults remain byte-identical — no reorder, no rename, no default change
  - [x] `.env.example` gains exactly five new entries at the bottom: `TARPIT_DETECTION_ENABLED=true`, `TARPIT_DETECTION_LOG_PATH=logs/detection.log`, `TARPIT_DETECTION_RATE_BUDGET=60`, `TARPIT_DETECTION_RATE_WINDOW_S=60.0`, `TARPIT_DETECTION_STATE_MAX_ENTRIES=10000`
  - [x] Existing `.env.example` entries unchanged
  - [x] `uv run python -c "from tarpit import detection; from tarpit.settings import get_settings; s = get_settings(); assert s.detection_enabled is True and s.detection_rate_budget == 60"` exits 0
  - [x] `uv run pytest -q` is green (no regressions — all 64 v1 tests pass)
- **Notes:** Pure plumbing. No new logic. Do **not** stub `state.py`, `signals.py`, `log.py`, or `middleware.py` — those are owned by T2/T3/T5/T7. The empty `__init__.py` is enough to make `tarpit.detection` an importable package for the downstream tasks.

---

## T2 — `detection/state.py` — in-memory per-IP state with LRU bound

- **Owns:**
  - `src/tarpit/detection/state.py` (create)
  - `tests/test_detection_state.py` (create)
- **Depends on:** T1
- **Parallel group:** W2a
- **Acceptance:**
  - [x] Public surface matches [design.md §2.2 `state.py`](./design.md#srctarpitdetectionstatepy) exactly: `class DetectionStateStore` with `__init__(max_entries, window_seconds, rate_budget)`, `record_request(ip, now)`, `is_rate_exceeded(ip, now)`, `has_prior_maze_visit(ip)`, `mark_maze_visit(ip)`, `__len__`
  - [x] `record_request` prunes timestamps older than `now - window_seconds` from the per-IP deque **before** appending `now`
  - [x] `record_request` moves the IP to most-recently-used in the LRU on every call
  - [x] `record_request` on a new IP when `len(store) == max_entries` evicts exactly one least-recently-used entry **before** inserting (so `len(store) <= max_entries` always holds) — REQ-STATE-03, REQ-SEC-01
  - [x] `is_rate_exceeded` returns `True` iff the pruned deque length is **strictly greater** than `rate_budget` (budget-count ties do NOT trip) — [design.md §6 "Rate window boundary"](./design.md#6-risks--edge-cases)
  - [x] `is_rate_exceeded` returns `False` for an unknown IP
  - [x] `has_prior_maze_visit` returns `False` for an unknown IP; returns `True` only after `mark_maze_visit` has been called for that IP
  - [x] `__len__` returns the current entry count
  - [x] **Determinism test**: `record_request` + eviction order is deterministic across runs for the same input sequence
  - [x] **Boundary test**: insert exactly `max_entries` IPs, then one more → `__len__ == max_entries`, the first IP inserted is evicted, the second-inserted IP is still present
  - [x] **Pruning test**: a deque with 100 timestamps spanning 120 seconds, queried at `now = 120`, with `window_seconds = 60` → pruned to only entries from `[60, 120]`
  - [x] No thread/async locks; no `threading.Lock` or `asyncio.Lock` in state.py — single-worker invariant from design §5 — reviewer MUST flag any lock import as FAIL
- **Notes:** Satisfies REQ-STATE-01 (in-memory), REQ-STATE-02 (process-lifetime discard — implicit, no persistence code), REQ-STATE-03 (LRU bound), REQ-SEC-01 (eviction behavior). Uses `collections.OrderedDict` + `collections.deque` only — no new dependencies. `time` is passed in by the caller as `now`, not read inside this module, so tests use fixed timestamps.

---

## T3 — `detection/log.py` — JSON-lines detection log writer

- **Owns:**
  - `src/tarpit/detection/log.py` (create)
  - `tests/test_detection_log.py` (create)
- **Depends on:** T1
- **Parallel group:** W2a
- **Acceptance:**
  - [x] Public surface matches [design.md §2.2 `log.py`](./design.md#srctarpitdetectionlogpy) exactly: `@dataclass(frozen=True) DetectionRecord` with fields `ts: str`, `ip: str`, `ua: str | None`, `path: str`, `triggers: list[str]`; `class DetectionLogWriter` with `__init__(file, lock)` and `async append(record)`
  - [x] Written JSON line matches [design.md §2.3 schema](./design.md#23-detection-log-json-schema-v21): key order exactly `ts, ip, ua, path, triggers`; one line, newline-terminated
  - [x] `ua=None` serialises as JSON `null`
  - [x] `triggers=["ua_missing", "rate_exceeded"]` serialises as a JSON array in that order
  - [x] `append` runs the blocking write + flush via `asyncio.to_thread` under the lock
  - [x] **Concurrent-append test**: fire 20 `append` calls on the same writer with `asyncio.gather`; file has exactly 20 lines, every line parses as JSON, no interleaved bytes — mirrors v1 `AccessLogWriter` concurrency test
  - [x] `DetectionRecord` is frozen (mutation attempt raises `FrozenInstanceError`)
  - [x] Writing a record with an empty `triggers` list is **not** forbidden at the API level (serialises as `[]`) — but middleware never does this (REQ-DLOG-02); documented as "API allows, caller discipline required"
- **Notes:** Satisfies REQ-DLOG-01, REQ-DLOG-04 (shape forbids extra fields by construction). Structurally parallel to v1 `AccessLogWriter`; reviewer may compare the two.

---

## T4 — Refactor `routers/maze.py` — extract `generate_maze_response`

- **Owns:**
  - `src/tarpit/routers/maze.py` (modify — refactor only)
- **Depends on:** T1
- **Parallel group:** W2a
- **Acceptance:**
  - [x] A new module-level `async def generate_maze_response(seed, chain, log_writer, settings, request)` function exists with the signature from [design.md §2.2 maze refactor](./design.md#srctarpitroutersmazepy--refactor-not-a-contract-change)
  - [x] The existing `GET /maze/{id}` route handler is reduced to a thin wrapper that calls `await generate_maze_response(id, chain, log_writer, settings, request)` and returns its result
  - [x] **v1 maze.py behavior is byte-identical**: `uv run pytest -q tests/test_maze.py` passes with **zero changes to `tests/test_maze.py`** — if the implementor modifies that test file, reviewer FAILs
  - [x] `generate_maze_response` is pure in the "no module-level state" sense — it takes all its collaborators as parameters, consistent with the design
  - [x] No change to any other v1 file (not `main.py`, not `renderer.py`, not `ids.py`, nothing else)
  - [x] `uv run pytest -q` is fully green after the refactor
- **Notes:** This is a pure extraction — zero semantic change. The function body IS the current handler body minus the `Depends` boilerplate; the wrapper is 2 lines. If the refactor requires altering `tests/test_maze.py` to pass, something has gone wrong in the extraction — stop and surface it (do **not** edit the test).

---

## T5 — `detection/signals.py` — rule evaluation

- **Owns:**
  - `src/tarpit/detection/signals.py` (create)
  - `tests/test_detection_signals.py` (create)
- **Depends on:** T2
- **Parallel group:** W2b
- **Acceptance:**
  - [ ] Public surface matches [design.md §2.2 `signals.py`](./design.md#srctarpitdetectionsignalspy) exactly: four `SIGNAL_*` string constants (`ua_missing`, `ua_known_bot_pattern`, `rate_exceeded`, `prior_maze_visit`), one `_UA_BOT_PATTERN` compiled regex (private), one `evaluate_request(request, store, now) -> list[str]` function
  - [ ] UA regex is `re.compile(r"bot|crawler|spider", re.IGNORECASE)` — matches REQ-DET-03 criteria exactly
  - [ ] `evaluate_request` is a pure read — no mutation of `store`, no I/O (reviewer must confirm no `store.record_request` / `store.mark_maze_visit` calls inside signals.py)
  - [ ] **Mutual exclusion test**: a request with `User-Agent: ""` returns `["ua_missing"]`, never `["ua_missing", "ua_known_bot_pattern"]`
  - [ ] **Trigger order test**: a request that trips all four signals (empty UA + rate exceeded + prior maze visit) returns a list in the stable order `["ua_missing", "rate_exceeded", "prior_maze_visit"]` — `ua_known_bot_pattern` cannot co-occur with `ua_missing`
  - [ ] **UA cases**: missing header → `["ua_missing"]`; empty-string header → `["ua_missing"]`; `"Googlebot/2.1"` → `["ua_known_bot_pattern"]`; `"Mozilla/5.0 ... Chrome/..."` on first visit → `[]`
  - [ ] **IP source**: `request.client.host or "unknown"` only — reviewer MUST confirm the module reads **no** request headers for IP identification (`X-Forwarded-For`, `X-Real-IP`, etc. never consulted) — REQ-SEC-02
  - [ ] Tests use a minimal `Request`-like stub (not a full FastAPI app) — unit speed only, no middleware involvement
- **Notes:** Satisfies REQ-DET-02 through REQ-DET-05 (signal evaluation) and REQ-DET-06 (OR composition — any signal fires → non-empty list). The hard-rules model (Q2a) is realised by appending to the list rather than scoring.

---

## T6 — `dependencies.py` — detection DI providers

- **Owns:**
  - `src/tarpit/dependencies.py` (modify — add two providers)
- **Depends on:** T2, T3
- **Parallel group:** W2b
- **Acceptance:**
  - [ ] Two new functions added: `get_detection_store(request) -> DetectionStateStore` and `get_detection_log_writer(request) -> DetectionLogWriter`
  - [ ] Each returns `request.app.state.detection_store` / `request.app.state.detection_log_writer` respectively, with the correct type annotation imported from `tarpit.detection.state` / `tarpit.detection.log`
  - [ ] Existing v1 providers (`get_chain`, `get_log_writer`, `get_settings_dep`) are untouched — signatures, bodies, order preserved
  - [ ] No new test file — these are one-liner delegates; coverage comes via middleware tests in T7 and e2e tests in T9
  - [ ] Smoke import: `uv run python -c "from tarpit.dependencies import get_detection_store, get_detection_log_writer"` exits 0
- **Notes:** The middleware (T7) does **not** use these providers — it reads `request.app.state` directly for performance. These exist for test overrides and any future admin routes.

---

## T7 — `detection/middleware.py` — the detection middleware + its tests

- **Owns:**
  - `src/tarpit/detection/middleware.py` (create)
  - `tests/test_detection_middleware.py` (create)
  - `tests/test_detection_switch.py` (create)
- **Depends on:** T2, T3, T4, T5
- **Parallel group:** W3 (serial — single task in wave)
- **Acceptance:**
  - [ ] Public API is a single function `register_detection_middleware(app: FastAPI) -> None` — no exported classes, no module-level globals that hold state
  - [ ] Middleware implements the nine-step ordering from [design.md §2.1](./design.md#21-middleware-contract) **exactly**:
    1. Short-circuit on `settings.detection_enabled == False` — no state mutation, no log
    2. Short-circuit on `request.url.path == "/robots.txt"` — no state mutation, no log
    3. `ip = request.client.host or "unknown"`
    4. `now = time.monotonic()`
    5. `store.record_request(ip, now)` **before** evaluate
    6. `triggers = evaluate_request(request, store, now)`
    7. If path starts with `/maze/`: `store.mark_maze_visit(ip)` **after** evaluate (so current request doesn't trip itself)
    8. If `triggers` non-empty: write detection log; if path matches `^/maze/[A-Za-z0-9._~-]{1,64}$` → `call_next`, else → `generate_maze_response(seed=BAIT_ID, ...)`
    9. Else: `call_next`
  - [ ] **Test matrix in `test_detection_middleware.py` (one test per flow from design §4):**
    - [ ] Non-flagged GET `/` (normal browser UA) → passes through, no detection log line, state records the request
    - [ ] Flagged GET `/` with missing UA → 200 maze HTML at path `/`, detection log line with `triggers=["ua_missing"]`, access log line with `path="/"` — [design.md §4.2](./design.md#42-flagged-on-a-non-maze-path-serves-maze-inline)
    - [ ] Flagged GET `/maze/a8f3` due to rate_exceeded (seed the state with 61 prior requests) → 200 maze HTML for seed `a8f3`, detection log line with `triggers=["rate_exceeded"]`, access log line with `path="/maze/a8f3"` — [design.md §4.3](./design.md#43-flagged-on-a-mazevalid-id-path-route-handles-it)
    - [ ] GET `/robots.txt` with missing UA → passes through, body is exact v1 robots.txt bytes, **no** detection log line, **no** state mutation (verify by checking `len(store) == 0` after the request)
    - [ ] Flagged GET `/maze/<invalid-id>` (e.g. `/maze/has%20space`) due to rate_exceeded → detection log line written **with `path="/maze/has%20space"`**, then 404 from v1's path-validation handler; **no** access log line (v1 only logs 200) — [design.md §6 "Flagged /maze/<invalid-id>"](./design.md#6-risks--edge-cases)
    - [ ] Multi-signal trip: request with missing UA + prior maze visit + rate exceeded → `triggers=["ua_missing", "rate_exceeded", "prior_maze_visit"]` in exact order
    - [ ] Flagged seed selection: when path is `/` and client is flagged, `generate_maze_response` is called with `seed=BAIT_ID` (`"root"`) — verified by checking that the returned body is identical to `GET /maze/root` output for the same chain
    - [ ] 2–5s delay applies on the non-maze flagged path too (REQ-RESP-05); tests set `TARPIT_DELAY_MIN_S=0 TARPIT_DELAY_MAX_S=0` to run fast
  - [ ] **Kill-switch tests in `test_detection_switch.py`:**
    - [ ] With `TARPIT_DETECTION_ENABLED=false`, a GET `/` with missing UA (would normally trip `ua_missing`) returns v1 content (contains the bait anchor), no detection log line, `len(store) == 0` (state untouched) — REQ-SWITCH-01, REQ-SWITCH-02
    - [ ] With `TARPIT_DETECTION_ENABLED=false`, `GET /maze/root` runs the normal maze handler (access log written, 2–5s delay applied)
  - [ ] Tests construct a minimal FastAPI app in-file, call `register_detection_middleware(app)`, and inject singletons via `app.state.*` — they do **not** import `tarpit.main.app`
  - [ ] Middleware does **not** import from `tarpit.dependencies` — reviewer MUST flag any such import as FAIL
- **Notes:** The single largest surface in v2.1. Builds the mini test app via a helper (one `make_app()` function inside each test file), mirrors how v1 `test_root.py` / `test_maze.py` are structured. For the "flagged seed selection" test: assert the response body equals the body of a direct call to `generate_maze_response(seed="root", ...)` with the same `rng` seeding — bit-for-bit equality.

---

## T8 — `README.md` — document kill switch, detection log, single-worker reminder

- **Owns:**
  - `README.md` (modify — add a v2.1 section; do not restructure v1 content)
- **Depends on:** T1
- **Parallel group:** W2b
- **Acceptance:**
  - [ ] A new section titled "Detection (v2.1)" (or similar) is appended to `README.md` after the existing v1 content
  - [ ] Documents `TARPIT_DETECTION_ENABLED` as the kill switch (default `true`; set `false` for incident recovery)
  - [ ] Documents the five `TARPIT_DETECTION_*` env vars with their defaults from design §2.2
  - [ ] Documents `logs/detection.log` location and schema (one JSON object per line with keys `ts, ip, ua, path, triggers`) — can link to [design.md §2.3](./design.md#23-detection-log-json-schema-v21) rather than duplicate the full schema
  - [ ] Includes a shell snippet showing how to tail both logs together: `tail -F logs/access.log logs/detection.log`
  - [ ] Reinforces the **single-worker** requirement: documents that `main.py` passes `workers=1` and warns that detection state is per-process; raising `--workers` would break detection and MUST NOT be done in v2.x
  - [ ] Does **not** modify the existing v1 Install / Run / Verify sections
- **Notes:** Satisfies REQ-DOC-01 (implicit — mirrors v1 docs practice). Reviewer MUST check that the single-worker warning is explicit and in the user's eyeline, not a footnote.

---

## T9 — `main.py` wire-up + end-to-end tests

- **Owns:**
  - `src/tarpit/main.py` (modify — lifespan + register call)
  - `tests/test_detection_e2e.py` (create)
- **Depends on:** T6, T7
- **Parallel group:** W4 (serial — single task in wave)
- **Acceptance:**
  - [ ] `lifespan(app)` in `main.py` extends v1's lifespan to additionally:
    - Construct `app.state.detection_store = DetectionStateStore(max_entries=settings.detection_state_max_entries, window_seconds=settings.detection_rate_window_s, rate_budget=settings.detection_rate_budget)`
    - Create `settings.detection_log_path.parent` if missing
    - Open the detection log file in append mode (UTF-8) and construct `app.state.detection_log_writer = DetectionLogWriter(detection_file, asyncio.Lock())`
    - Close the detection log file on shutdown — symmetric with the v1 access log close
  - [ ] `create_app()` calls `register_detection_middleware(app)` after both routers are included but before the `RequestValidationError` handler is registered (so the middleware wraps the router + handler chain)
  - [ ] All v1 behavior in `main.py` preserved: `workers=1` still passed to `uvicorn.run`; v1 lifespan setup for `chain` and `log_writer` unchanged; `app = create_app()` module-level still exists; `__init__.py` exports unchanged
  - [ ] **End-to-end tests in `test_detection_e2e.py`** (boots real uvicorn on ephemeral port, mirrors `test_e2e_smoke.py` style):
    - [ ] Lifespan builds both state store and detection log writer: on startup, `app.state.detection_store` and `app.state.detection_log_writer` both exist and are of the correct types
    - [ ] A real request to `/` with no `User-Agent` header receives 200 maze HTML at path `/`, `logs/detection.log` gains one line with `triggers=["ua_missing"]`, `logs/access.log` gains one line with `path="/"`
    - [ ] A real request to `/robots.txt` with no `User-Agent` header receives the normal v1 robots.txt bytes, `logs/detection.log` is unchanged, `logs/access.log` is unchanged
    - [ ] With `TARPIT_DETECTION_ENABLED=false`, a real request to `/` with no `User-Agent` header receives the v1 `/` content (contains the bait anchor), both logs unchanged
    - [ ] Tests force `TARPIT_DELAY_MIN_S=0 TARPIT_DELAY_MAX_S=0` and use `tmp_path`-based `TARPIT_LOG_PATH` / `TARPIT_DETECTION_LOG_PATH` so they do not pollute real log files
  - [ ] After this task, `uv run pytest -q` is fully green including all v1 and v2.1 tests
- **Notes:** Final integration. The v1 e2e smoke test (`test_e2e_smoke.py`) is **not** modified — v2.1 adds its own e2e file. If the v1 e2e test flakes after wiring (e.g. because detection now sits in front of its requests), investigate; most likely root cause is the test's requests tripping `prior_maze_visit` on sequential `/maze/*` calls, which still produces valid responses but adds a detection log line. If detection must be disabled for v1 e2e to isolate, that's a separate follow-up — surface it to the user, don't modify `test_e2e_smoke.py` silently.

---

## Execution plan

Agent cap: **3 concurrent implementors per wave**, carried over from v1.

- **Wave 1** (serial): **T1** — scaffold (detection package + settings fields + .env entries)
- **Wave 2a** (parallel group W2a, 3 agents): **T2** state, **T3** log, **T4** maze refactor
- **Wave 2b** (parallel group W2b, 3 agents): **T5** signals, **T6** dependencies, **T8** README
- **Wave 3** (serial): **T7** — middleware + its tests + switch tests
- **Wave 4** (serial): **T9** — main.py wire-up + e2e

Max fan-out: **3 agents** (in W2a and W2b).

## Risks for execution

- **T4 is a stealth landmine.** The refactor must preserve v1 maze behavior byte-for-byte; the only legitimate signal of a successful refactor is `tests/test_maze.py` passing **unchanged**. If an implementor "fixes" tests to work around a behavior drift, that's exactly the failure mode CLAUDE.md's "never alter a test to make it pass" rule exists to catch. Reviewer MUST diff `tests/test_maze.py` and FAIL on any non-whitespace change.
- **T5 UA regex false positives.** The regex `bot|crawler|spider` matches legitimate crawlers (Googlebot). This is intentional for v2.1 per the resolved Q-OPEN-1 decision (deferred to the public-deployment spec). Reviewer should NOT suggest a carve-out — flagging Googlebot is correct v2.1 behavior.
- **T7's test for the flagged `/` seed selection is subtle.** The acceptance item "when path is `/` and client is flagged, body equals a direct `generate_maze_response(seed='root', ...)` call" requires both calls to use the same `chain` instance so Markov generation is identical. If the test constructs two chains it will fail — reviewer should check that one chain is reused.
- **T7's `/maze/<invalid>` flagged test is the trickiest flow.** The middleware marks `mark_maze_visit` for any path starting with `/maze/` (including invalid ids), writes a detection log line, then forwards to `call_next` which hits v1's path-validation 404 handler. The detection log IS written; the access log is NOT. If the implementor forgets the step-7 `mark_maze_visit` runs for invalid paths too, the test will subtly fail only on **subsequent** requests (which would then not trip `prior_maze_visit`). Make this explicit in the test with two sequential requests from the same IP: first `/maze/<invalid>` → 404 + detection log line + state mutation; second `/` → detection log line with `triggers=["prior_maze_visit", ...]`.
- **T9's e2e tests must use `tmp_path` for log files.** If they don't, each test run pollutes real `logs/access.log` and `logs/detection.log`. Mirror the pattern from v1 `test_e2e_smoke.py` with env var overrides.
- **Multi-worker footgun persists.** v1 enforced `workers=1`. v2.1 depends on this harder — detection state is per-process. T8 (README) and T9 (main.py preservation of `workers=1`) are both parts of the mitigation. Any implementor who proposes removing `workers=1` or adding a `workers` setting must be reviewer-FAILed, same policy as v1.
- **`src/tarpit/detection/__init__.py` is co-owned across T1 (create empty) and no one else.** No file-ownership conflict in the current plan; just noted so a future wave reorder doesn't accidentally reintroduce one.
- **T6 is tiny and might feel like filler.** It is small — two one-line delegates. Keep it a separate task anyway so file ownership stays clean; folding it into T5 or T7 creates a shared `dependencies.py` edit across tasks that would otherwise be independent.
