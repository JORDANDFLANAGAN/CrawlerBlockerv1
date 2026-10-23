# Abuse Protection (v2.2) — Tasks

**Status:** Draft · **Date:** 2026-10-23 · **Design:** [design.md](./design.md)

## Legend
- `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked
- `parallel: <group>` — tasks sharing a group id can run concurrently
- `depends_on: <ids>` — must complete before this task starts

---

## T1 — Settings: abuse-protection fields
- **Owns:** `src/tarpit/settings.py`
- **Depends on:** —
- **Parallel group:** A
- **Acceptance:**
  - [x] All eight fields from [design.md §2.1](./design.md#21-settings--additions-to-srctarpitsettingspy) added to `Settings`: `abuse_protection_enabled`, `abuse_log_path`, `rl_per_ip_budget`, `rl_per_ip_window_s`, `rl_state_max_entries`, `cc_per_ip_max`, `gl_inflight_max`, `gl_rate_budget`, `gl_window_s` with the exact defaults listed.
  - [x] Field names and types match the design (so env vars resolve as `TARPIT_ABUSE_PROTECTION_ENABLED`, `TARPIT_RL_PER_IP_BUDGET`, etc.).
  - [x] Existing fields and defaults untouched.
  - [x] `uv run pytest -q` still green.
- **Notes:** No validators. CLAUDE.md: trust internal code at this boundary. Verifies REQ-SWITCH-01, REQ-RL-03/04, REQ-CC-02, REQ-GL-02/03, REQ-LOG-01.

## T2 — Abuse package skeleton + reason constants
- **Owns:** `src/tarpit/abuse/__init__.py`, `src/tarpit/abuse/reasons.py`
- **Depends on:** —
- **Parallel group:** A
- **Acceptance:**
  - [x] Package directory `src/tarpit/abuse/` exists with an empty `__init__.py` (mirroring `src/tarpit/detection/__init__.py`).
  - [x] `reasons.py` exports exactly the four `REASON_*: Final[str]` constants from [design.md §2.2](./design.md#22-reason-constants--srctarpitabusereasonspy) with the literal string values pinned by REQ-LOG-02.
- **Notes:** Trivially small, intentionally separate so downstream tasks (T4, T5, T6) can import constants without waiting on the store or writer.

## T3 — Detection signal entry point
- **Owns:** `src/tarpit/detection/state.py`, `tests/test_detection_state.py`
- **Depends on:** —
- **Parallel group:** A
- **Acceptance:**
  - [x] `DetectionStateStore.flag_for_detection(ip: str, now: float) -> None` added per [design.md §2.6](./design.md#26-signal-to-detection--new-method-on-detectionstatestore). Creates the record if absent, touches LRU, applies the same eviction rule as `record_request`, sets `has_visited_maze = True`.
  - [x] New test(s) in `tests/test_detection_state.py` cover: unknown-IP creates record; known-IP flips the flag; LRU eviction fires when the new record pushes size over `max_entries`.
  - [x] All existing `DetectionStateStore` tests still pass.
- **Notes:** Keep the method signature exactly as frozen in design — T6 imports it. Do NOT modify `record_request`, `is_rate_exceeded`, `has_prior_maze_visit`, or `mark_maze_visit`.

## T4 — AbuseStateStore
- **Owns:** `src/tarpit/abuse/state.py`, `tests/test_abuse_state.py`
- **Depends on:** —
- **Parallel group:** A
- **Acceptance:**
  - [x] `AbuseStateStore` class with the exact `__init__` kwargs and public methods from [design.md §2.3](./design.md#23-abusestatestore--srctarpitabusestatepy): `record_request`, `is_per_ip_rate_exceeded`, `is_global_rate_exceeded`, `try_acquire_slot`, `release_slot`, `per_ip_inflight`, `global_inflight`, `__len__`.
  - [x] `try_acquire_slot` returns `None` on success; on failure returns `REASON_PER_IP_CONCURRENCY` *before* `REASON_GLOBAL_INFLIGHT` (check order fixed by design).
  - [x] `release_slot` clamps per-IP decrement at 0 and decrements global unconditionally (handles the LRU-eviction race called out in [design.md §6](./design.md#6-risks--edge-cases)).
  - [x] `record_request` updates both the per-IP deque and the global deque in one call; prunes both using their respective window settings; evicts LRU on overflow.
  - [x] Tests cover: fresh-IP record creation, sliding-window prune, per-IP rate threshold (strict `>` budget), global rate threshold, concurrency acquire/release pairing, concurrency ordering (per-IP blocks before global), LRU eviction, and the eviction-mid-flight race (acquire, force eviction via N other IPs, release still works).
- **Notes:** No locks, no `await`. Mirror the `DetectionStateStore` style (`OrderedDict`, `deque`, synchronous methods). Import the reason constants from `tarpit.abuse.reasons` — introduces a soft dependency on T2 if T2 lands first, otherwise stub-import later. If T2 isn't ready at implementation start, inline-define the two needed constants temporarily and swap imports after T2.

## T5 — AbuseLogWriter + AbuseRecord
- **Owns:** `src/tarpit/abuse/log.py`, `tests/test_abuse_log.py`
- **Depends on:** —
- **Parallel group:** A
- **Acceptance:**
  - [x] `AbuseRecord` frozen dataclass with field order `ts, ip, ua, path, reason` matching [design.md §2.4](./design.md#24-log-record--writer--srctarpitabuselogpy).
  - [x] `AbuseLogWriter.append` writes one JSON line, newline-terminated, key order `ts, ip, ua, path, reason`, flushed; blocking IO goes through `asyncio.to_thread` under the writer's `asyncio.Lock`.
  - [x] Tests assert: single-line JSON output; key order; concurrent `append` calls from two tasks produce two complete, non-interleaved lines; `ua=None` serialises to JSON `null`.
- **Notes:** Mirror `DetectionLogWriter` structurally. Verifies REQ-LOG-01..04.

## T6 — Abuse middleware
- **Owns:** `src/tarpit/abuse/middleware.py`, `tests/test_abuse_middleware.py`
- **Depends on:** T2, T3, T4, T5
- **Parallel group:** B
- **Acceptance:**
  - [x] `register_abuse_middleware(app: FastAPI) -> None` installs an HTTP middleware whose behaviour follows the nine-step sequence in [design.md §2.5](./design.md#25-middleware--srctarpitabusemiddlewarepy) exactly: kill-switch pass-through, `/robots.txt` pass-through, scope check (`/` or `/maze/*`), `record_request`, then per-IP rate → global rate → `try_acquire_slot` in that order, 429 on breach with log + `flag_for_detection` call, otherwise `try/finally` around `call_next` to guarantee `release_slot`.
  - [x] 429 response has status 429, empty body, `Content-Type: text/plain; charset=utf-8`, `Content-Length: 0`, no `Retry-After` ([design.md §2.7](./design.md#27-response-shape)).
  - [x] `flag_for_detection` call is skipped when `settings.detection_enabled` is `False` (REQ-SIG-04).
  - [x] Middleware reads settings via the same `app.state.settings` → `get_settings()` fallback pattern used in `detection/middleware.py`.
  - [x] Tests (middleware-level, using `TestClient` with injected store/writer on `app.state`) cover: per-IP rate 429, global rate 429, per-IP concurrency 429, global concurrency 429, `/robots.txt` always allowed, out-of-scope path allowed, kill-switch off bypasses everything, 429 writes exactly one line to the abuse log with the correct `reason`, 429 calls `flag_for_detection` on the detection store, kill-switched detection suppresses the signal.
- **Notes:** Do NOT touch `main.py` here — T7 wires it. Build the test fixtures so they stand up a minimal `FastAPI()` app with just this middleware and a dummy `/maze/{x}` + `/` + `/robots.txt` route; no need to boot `create_app()`.

## T7 — Lifespan wiring in main.py
- **Owns:** `src/tarpit/main.py`
- **Depends on:** T1, T4, T5, T6
- **Parallel group:** —
- **Acceptance:**
  - [x] `lifespan` constructs `AbuseStateStore` with every kwarg mapped from settings per [design.md §2.8](./design.md#28-lifespan-wiring--srctarpitmainpy), opens `logs/abuse.log` (parent `mkdir(parents=True, exist_ok=True)`), and attaches `app.state.abuse_store` and `app.state.abuse_log_writer`. File is closed in shutdown.
  - [x] `create_app()` calls `register_detection_middleware(app)` *before* `register_abuse_middleware(app)` so abuse runs first at request time (FastAPI reverses order).
  - [x] `uv run pytest -q` still green with no abuse-protection-specific tests disabled; existing detection e2e tests still pass unchanged.
- **Notes:** Single-file task so no one else needs to edit `main.py` in parallel. If a test currently inspects `app.state` fields, add the new fields rather than renaming existing ones.

## T8 — E2E + middleware-ordering tests
- **Owns:** `tests/test_abuse_e2e.py`, `tests/test_abuse_middleware_ordering.py`
- **Depends on:** T7
- **Parallel group:** C
- **Acceptance:**
  - [ ] E2E test boots the real `create_app()` (via the existing TestClient pattern) and exercises: baseline 200 under limits; sustained requests from one IP eventually produce 429 with empty body on both `/` and `/maze/{id}`; `GET /robots.txt` is never 429'd even when caps are tripped; a 429 appends a line to `logs/abuse.log` with the correct keys and reason.
  - [ ] Ordering test confirms abuse runs before detection: configure thresholds so abuse would 429 but detection *would* flag with maze content; assert the response is 429 (empty body), and that `logs/detection.log` did NOT receive a line for that request. After relaxing abuse thresholds (next request), confirm detection then fires on `prior_maze_visit` because `flag_for_detection` was called on the earlier 429.
  - [ ] Tests use temporary log paths via settings override; do not write under the repo's real `logs/` directory.
- **Notes:** Verifies REQ-SCOPE-01/02/03, REQ-SIG-01, REQ-GL-04, REQ-RESP-01/02/03. Use `pytest` fixtures (not mocks) per CLAUDE.md.

## T9 — Kill-switch test
- **Owns:** `tests/test_abuse_switch.py`
- **Depends on:** T7
- **Parallel group:** C
- **Acceptance:**
  - [ ] With `TARPIT_ABUSE_PROTECTION_ENABLED=false`: a client exceeding every documented threshold still receives 200 and `logs/abuse.log` remains empty for the duration of the test.
  - [ ] With abuse disabled, detection still fires normally (REQ-SWITCH-02): assert a `rate_exceeded`-flagged request produces a `logs/detection.log` line.
  - [ ] With `TARPIT_DETECTION_ENABLED=false` but abuse enabled: 429s still happen, `logs/abuse.log` still receives lines, and no call to `flag_for_detection` leaks state (verify by sending a slow-paced follow-up request from the same IP and asserting it is NOT flagged with `prior_maze_visit`).
- **Notes:** Verifies REQ-SWITCH-01/02, REQ-SIG-04. Mirror `tests/test_detection_switch.py` for style and layout.

## T10 — README documentation
- **Owns:** `README.md`
- **Depends on:** T7
- **Parallel group:** C
- **Acceptance:**
  - [ ] New `## Abuse protection (v2.2)` section parallel in structure to the existing `## Detection (v2.1)` section (Q-RESOLVED-7).
  - [ ] Configuration table listing all eight `TARPIT_` env vars from T1 with defaults and one-line descriptions.
  - [ ] Short explanation of the four reasons that appear in `logs/abuse.log` and how to tail it.
  - [ ] Explicit callout of the reverse-proxy caveat (Q-OPEN-1): behind a proxy without trusted-header parsing, per-IP caps collapse to global caps; defaults are tuned for a local single-user deployment and MUST be re-tuned before any public deployment.
  - [ ] Note that the two layers are independently togglable and reference both env flags.
- **Notes:** No code changes. Pure documentation.

---

## Execution plan
- **Wave 1 (parallel group A):** T1, T2, T3, T4, T5 — five independent files/modules; no shared ownership. T4 and T5 may import constants from T2 (same wave): acceptable because the import target is a tiny module with no logic risk, but if an implementor hits a race, T4/T5 can inline-define the two needed constants and swap imports in a trivial follow-up.
- **Wave 2 (serial):** T6 — middleware; depends on T2–T5.
- **Wave 3 (serial):** T7 — lifespan + ordering wiring; depends on T1, T4, T5, T6 and owns `main.py` alone.
- **Wave 4 (parallel group C):** T8, T9, T10 — e2e tests, kill-switch tests, and README; three distinct files/trees, no overlap.

## Risks for execution
- **T4/T5 ↔ T2 import race in Wave 1.** All three run in parallel in group A. T4 and T5 import `REASON_*` constants from `tarpit.abuse.reasons` (T2). If an implementor opens T4 before T2 has committed, their tests will fail import. Mitigation: implementors in Wave 1 may stub the two constants locally and open a trivial follow-up PR after T2 lands — do not block the wave.
- **T3 and other detection tests.** T3 edits `tests/test_detection_state.py`. No other task in Wave 1 touches detection tests, but reviewers must confirm no existing assertions were weakened to accommodate the new method.
- **T6 test fixture scope.** T6's tests build a minimal `FastAPI()` app rather than booting `create_app()` to keep the test target narrow. This means T6 does NOT exercise registration order — that's T8's ordering test. Reviewers must not reject T6 for missing ordering coverage.
- **T8 ordering test is the only guard** against someone later swapping the registration lines in `main.py`. Keep its assertion specific (detection log untouched by the 429'd request) rather than inspecting `app.user_middleware` internals, which FastAPI reserves the right to change.
- **Shared log directory in tests.** T8/T9 must use temp paths via settings override; otherwise parallel pytest runs across waves corrupt each other's `logs/abuse.log` fixture expectations.
