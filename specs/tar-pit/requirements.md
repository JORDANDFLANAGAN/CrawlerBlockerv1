# Tar Pit v1

**Status:** Draft · **Owner:** @Jordan · **Date:** 2026-10-21 · **Source:** `.claude/specs/TarPit.md`

## 1. Problem

- Web crawlers that ignore `robots.txt` consume bandwidth, generate log noise, and may feed low-value scrape content into third-party training pipelines.
- Classifying "is this request a bot?" is error-prone and expensive — this project avoids classification entirely.
- Instead, `robots.txt` + a hidden link define rule-breakers *by construction*: compliant crawlers won't follow a disallowed path, humans won't see an invisible anchor, so only clients that ignore both end up inside the maze.
- Once inside, each request costs the operator negligible async wait time and costs the crawler a 2–5s held TCP connection, producing a per-request log line as a side effect.
- v1 goal is to validate the mechanism locally before anything is deployed publicly.

## 2. Goals & Non-goals

### Goals
- Operator can run the service locally and observe that a `curl` loop following links never terminates.
- Every page under `/maze/` is byte-identical on repeat fetches of the same URL.
- Every `/maze/` response is delayed by a random 2–5 seconds, implemented so one slow request does not block other requests.
- Every `/maze/` request produces a structured JSON log line with timestamp, IP, user agent, path, and applied delay.
- Compliant crawlers are given a clear `robots.txt` signal to stay out of `/maze/`.
- A new reader can clone the repo, run one command, and verify the Done-when checklist from the source spec.

### Non-goals
- Bot detection, fingerprinting, or any attempt to classify "is this a bot?".
- Public deployment, reverse-proxy configuration, TLS, or hosting advice.
- Rate limiting, concurrent-connection caps, or abuse protection against the tar pit itself.
- Tuning content to "poison" AI training sets.
- Any analytics UI or log-analysis script.
- Persisting generated pages to a database or cache.
- Any claim or measurement that this degrades downstream AI model quality.

## 3. Functional requirements

### ROBOTS

- **REQ-ROBOTS-01** · THE SYSTEM SHALL serve `GET /robots.txt` with a body containing `User-agent: *` followed by `Disallow: /maze/`, as a `text/plain` response.
  *Source:* Rule / Endpoints table · *Verified by:* test to be defined

### BAIT

- **REQ-BAIT-01** · THE SYSTEM SHALL serve `GET /` with an HTML page that contains at least one anchor element whose `href` resolves to a path under `/maze/`.
  *Source:* Bait / Endpoints table · *Verified by:* test to be defined
- **REQ-BAIT-02** · THE SYSTEM SHALL render the bait anchor with `style="display:none"` (inline), so it is not visible in a standard browser render.
  *Source:* Bait + clarification Q3 · *Verified by:* test to be defined
- **REQ-BAIT-03** · THE SYSTEM SHALL point the bait anchor at a fixed, well-known seed value (e.g. `/maze/root`), so the trap entry point is deterministic.
  *Source:* Clarification Q2 · *Verified by:* test to be defined
- **REQ-BAIT-04** · THE SYSTEM SHALL render on `/` a short block of visible human copy that includes a line stating that a viewer who can read it is likely a human (and not a poorly behaved bot).
  *Source:* Clarification Q5 · *Verified by:* test to be defined

### MAZE

- **REQ-MAZE-01** · THE SYSTEM SHALL expose `GET /maze/{id}` where `{id}` is any URL-safe string.
  *Source:* Endpoints table + clarification Q2 · *Verified by:* test to be defined
- **REQ-MAZE-02** · WHEN `GET /maze/{id}` is served, THE SYSTEM SHALL produce a response body that is a pure deterministic function of `{id}`, such that two requests with the same `{id}` yield byte-identical response bodies.
  *Source:* Key decision: deterministic pages · *Verified by:* test to be defined
- **REQ-MAZE-03** · WHEN rendering `/maze/{id}`, THE SYSTEM SHALL emit between 5 and 10 anchor elements, each with an `href` pointing to a distinct `/maze/{new_id}` path.
  *Source:* Request flow step 3 · *Verified by:* test to be defined
- **REQ-MAZE-04** · WHEN rendering `/maze/{id}`, THE SYSTEM SHALL compute each `{new_id}` deterministically from `{id}` using the request-scoped seeded RNG, so link targets are stable across repeat fetches.
  *Source:* Key decision + "Done when" determinism bullet · *Verified by:* test to be defined
- **REQ-MAZE-05** · THE SYSTEM SHALL guarantee that every `/maze/{id}` response contains at least one outgoing `/maze/` link, so a crawler following links never reaches a terminal page.
  *Source:* "Done when" never-dead-end bullet · *Verified by:* test to be defined
- **REQ-MAZE-06** · WHEN seeding its RNG for a request, THE SYSTEM SHALL instantiate a per-request `random.Random({id})` and SHALL NOT mutate or depend on the module-level `random` state.
  *Source:* Markov chain "Seeded" bullet · *Verified by:* test to be defined
- **REQ-MAZE-07** · IF `{id}` exceeds 64 characters, OR IF `{id}` contains any character outside the URL-safe set `[A-Za-z0-9._~-]`, THEN THE SYSTEM SHALL return HTTP 404 without generating content and without writing an access log line.
  *Source:* Design decision Q1 · *Verified by:* test to be defined

### GEN

- **REQ-GEN-01** · THE SYSTEM SHALL read `corpus.txt` exactly once at process startup and build an order-2 Markov chain held in process memory.
  *Source:* Markov chain · *Verified by:* test to be defined
- **REQ-GEN-02** · WHEN rendering `/maze/{id}`, THE SYSTEM SHALL emit approximately 200 words (±10%) of body text produced by the preloaded chain and the request-scoped RNG.
  *Source:* Request flow step 2 · *Verified by:* test to be defined
- **REQ-GEN-03** · THE SYSTEM SHALL select the Markov starting pair using the request-scoped RNG, so generated text is reproducible per `{id}`.
  *Source:* Markov chain "Generating" bullet · *Verified by:* test to be defined

### DELAY

- **REQ-DELAY-01** · WHEN responding to `/maze/{id}`, THE SYSTEM SHALL delay the response by a value sampled uniformly between 2.0 and 5.0 seconds inclusive.
  *Source:* Request flow step 5 · *Verified by:* test to be defined
- **REQ-DELAY-02** · THE SYSTEM SHALL implement the delay with non-blocking async I/O (e.g. `await asyncio.sleep(...)`), so a single slow `/maze/` request does not delay other in-flight requests.
  *Source:* "Slowness" bullet + "costs the server almost nothing" · *Verified by:* test to be defined

### LOG

- **REQ-LOG-01** · WHEN a `/maze/{id}` response is served, THE SYSTEM SHALL append exactly one JSON object (one line, newline-terminated) to the access log containing the fields `ts` (ISO-8601 UTC string), `ip` (string), `ua` (string or `null`), `path` (string), and `delay_s` (number).
  *Source:* Log sample · *Verified by:* test to be defined
- **REQ-LOG-02** · THE SYSTEM SHALL write the access log to `logs/access.log` relative to the application's working directory.
  *Source:* Clarification Q4 (recommendation accepted) · *Verified by:* test to be defined
- **REQ-LOG-03** · THE SYSTEM SHALL NOT write request bodies, query strings, cookies, or arbitrary headers beyond the user-agent to the access log.
  *Source:* CLAUDE.md security policy · *Verified by:* test to be defined

### SEC

- **REQ-SEC-01** · THE SYSTEM SHALL HTML-escape any value derived from `{id}` or Markov output before inserting it into a response body, so neither corpus content nor path parameters can inject executable markup.
  *Source:* CLAUDE.md attack-surface rule "validate every external input" + XSS risk implicit in reflecting `{id}` into HTML anchors · *Verified by:* test to be defined
- **REQ-SEC-02** · THE SYSTEM SHALL NOT echo stack traces or framework internals in `/maze/{id}` responses when generation fails; the request SHALL return an opaque 500 and SHALL log the failure server-side.
  *Source:* CLAUDE.md attack-surface rule "don't echo errors verbatim" · *Verified by:* test to be defined

### DOC

- **REQ-DOC-01** · THE SYSTEM SHALL ship a `README.md` describing: how to install deps with `uv`, how to run the service locally, the three endpoints, where the access log is written, and how to execute every item in the Done-when checklist.
  *Source:* Clarification Q4 (user asked for README documentation) · *Verified by:* test to be defined

## 4. Non-functional requirements

- **Concurrency**: a single uvicorn worker SHALL serve at least one `GET /` request within 200ms while N `/maze/{id}` requests are in their delay window. (Design to fix N; the point is "slow requests don't block fast ones".) *Source:* Request flow step 5.
- **Startup**: Markov training SHALL complete at process startup, so first-request latency is only the configured 2–5s delay. *Source:* Markov chain + Request flow.
- **Determinism**: identical `{id}` SHALL produce identical response *bodies* across process restarts; response *timing* (delay) MAY vary. *Source:* "Done when" determinism bullet; delay randomness clarified as orthogonal to body determinism.
- **Portability**: no OS-specific syscalls; service SHALL run on macOS and Linux unmodified. *Source:* local-only v1 scope.

## 5. Constraints

- Python 3.13, FastAPI, uvicorn, `uv` for dep management. *Source:* project CLAUDE.md + existing scaffolding.
- Markov corpus is Moby-Dick plain text (public domain). *Source:* Markov chain.
- Markov order is **2**; order 1 and order 3+ are explicitly rejected. *Source:* Markov chain "Order 2".
- Deterministic pages via per-request `random.Random(seed)`; module `random` state MUST NOT be used. *Source:* Markov chain "Seeded".
- Delay bounds are **2–5 seconds**; delay is **async**. *Source:* "Slowness" + request flow.
- Each `/maze/{id}` page emits **5–10** outgoing links. *Source:* Request flow step 3.
- `{new_id}` encoding is **4-character lowercase hex** (`[0-9a-f]{4}`), matching the source spec sample. *Source:* Design decision Q2.
- `{id}` accepted format on input is URL-safe chars `[A-Za-z0-9._~-]`, max 64 chars; anything outside this returns 404 (see REQ-MAZE-07). *Source:* Design decision Q1.
- `robots.txt` body is exactly `User-agent: *\nDisallow: /maze/\n`; no other paths listed. *Source:* Design decision Q5.
- Repo module path is **`src/tarpit/`**; the `crawlerblocker` package is removed and `[project.name]` is renamed to `tarpit`. *Source:* Design decision Q4.
- Access log is append-only `logs/access.log` relative to CWD; no rotation in v1. *Source:* Clarification Q4 (recommendation accepted).
- v1 is **local-only**. Everything in the source spec's "Out of scope for v1" list is a hard constraint for this requirements doc: no bot detection, no fingerprinting, no training-data pollution tuning, no rate limiting, no reverse-proxy setup, no public deployment. *Source:* Out of scope for v1.
- v2 items from the source spec are explicitly out of scope here. *Source:* Scope for v2.

## 6. Open questions

None open — all pre-design questions (Q1 `{id}` validation, Q2 `{new_id}` encoding, Q3 log rotation, Q4 repo module path, Q5 robots.txt scope) resolved and recorded as constraints before entering design.
