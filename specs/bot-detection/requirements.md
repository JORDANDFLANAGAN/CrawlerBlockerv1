# Bot Detection (v2.1)

**Status:** Draft · **Owner:** @Jordan · **Date:** 2026-10-21 · **Source:** `.claude/specs/BotDetection.md`

## 1. Problem

- v1 only catches crawlers that both ignore `robots.txt` AND follow a `display:none` link. Many misbehaving crawlers skip hidden anchors and never trip the trap.
- Without a detection layer, misbehaving crawlers can scrape `/` repeatedly and never land in the maze, defeating the point.
- v1 has no observability on "who is scraping me and getting away with it" — the access log only sees maze visitors.
- Serving a real `/` page to high-volume bots wastes bandwidth once the service is publicly deployed (v2 item #1).

## 2. Goals & Non-goals

### Goals
- A client with no / forged UA, excessive request rate, or a prior `/maze/*` request is routed into the maze on its next request — without needing to follow the hidden bait link.
- A normal browser making paced requests continues to see `/` unchanged.
- The three v1 endpoint contracts (`GET /robots.txt`, `GET /`, `GET /maze/{id}`) stay byte-compatible for non-flagged clients.
- Operators can read a dedicated detection log and audit which signals routed a given client into the maze.
- Operators can turn detection off entirely via a single config flag for incident recovery, without a code change.
- v1 bait anchor on `/` stays in place; detection is additive.

### Non-goals
- CAPTCHAs or any interactive challenge.
- IP reputation lookups against external services (deferred to a later v2.x).
- Rate limiting of maze connections themselves (that's v2.2, separate feature).
- Rewriting or re-tuning the maze content.
- Logging normal (non-flagged) traffic.
- Logging detection decisions that let a client through (only route-into-maze events are logged).
- Any recovery mechanism for a mis-flagged client beyond process restart.
- Public deployment / reverse-proxy integration (that's v2 item #1 and comes separately).

## 3. Functional requirements

### DET — Detection

- **REQ-DET-01** · WHEN a request arrives at any route other than `GET /robots.txt`, THE SYSTEM SHALL evaluate it against the three detection signals (UA, request rate, prior-`/maze/` visit) before invoking the normal handler.
  *Source:* "detection in front of v1" + Done-when #1–#3 · *Verified by:* test to be defined
- **REQ-DET-02** · IF the request's `User-Agent` header is missing or an empty string, THEN THE SYSTEM SHALL flag the request (signal id: `ua_missing`).
  *Source:* Done-when #1 + Q6b · *Verified by:* test to be defined
- **REQ-DET-03** · IF the request's `User-Agent` header, lowercased, contains any of the substrings `bot`, `crawler`, or `spider`, THEN THE SYSTEM SHALL flag the request (signal id: `ua_known_bot_pattern`).
  *Source:* Done-when #1 + Q6b · *Verified by:* test to be defined
- **REQ-DET-04** · IF the request's source IP has made more than a configured budget of requests inside a configured sliding window, THEN THE SYSTEM SHALL flag the request (signal id: `rate_exceeded`). The budget and window are design/settings decisions.
  *Source:* Done-when #2 · *Verified by:* test to be defined
- **REQ-DET-05** · IF the request's source IP has previously requested any path matching `^/maze/.*` during the current process lifetime, THEN THE SYSTEM SHALL flag the request (signal id: `prior_maze_visit`).
  *Source:* Done-when #3 + Q1a (process-lifetime retention) · *Verified by:* test to be defined
- **REQ-DET-06** · WHEN **any** of REQ-DET-02 through REQ-DET-05 is true for a request, THE SYSTEM SHALL treat the request as flagged. Signals compose by OR; the first-matching rule is sufficient to flag.
  *Source:* Q2a (hard rules, any signal trips) · *Verified by:* test to be defined

### STATE — Per-IP state tracking

- **REQ-STATE-01** · THE SYSTEM SHALL maintain all per-source-IP detection state (rate counters, prior-visit flag) in process memory only. No file, database, or external cache.
  *Source:* Q1a + "No new dependencies beyond v1" (NFR) · *Verified by:* test to be defined
- **REQ-STATE-02** · ON process restart, THE SYSTEM SHALL discard all per-IP state. Any previously-flagged IP returns to a clean slate; this is the only recovery mechanism in v2.1.
  *Source:* Q1a + Q4a · *Verified by:* test to be defined
- **REQ-STATE-03** · THE SYSTEM SHALL bound the per-IP state table to a configured maximum entry count, evicting least-recently-seen entries when the bound is reached. The bound is a settings decision.
  *Source:* Threat T1 (unbounded in-memory state → memory exhaustion) · *Verified by:* threat T1
- **REQ-STATE-04** · THE SYSTEM SHALL derive the source IP from `request.client.host` only. No trusted-header parsing (e.g. `X-Forwarded-For`) in v2.1.
  *Source:* Q4a context + Not-in-scope (no proxy integration in this feature) · *Verified by:* test to be defined

### RESP — Response for flagged requests

- **REQ-RESP-01** · WHEN a request is flagged, THE SYSTEM SHALL respond with HTTP `200 OK`, `Content-Type: text/html; charset=utf-8`, and a body produced by the v1 maze renderer, at the originally-requested path (no redirect).
  *Source:* Q3b · *Verified by:* test to be defined
- **REQ-RESP-02** · WHEN a flagged request's path does not already carry a valid maze `{id}` (i.e. the path is not `/maze/{id}` with `{id}` matching the v1 ID pattern), THE SYSTEM SHALL use `BAIT_ID` (`"root"`) as the maze seed.
  *Source:* Q3b implementation needs a seed; `BAIT_ID` already exists in v1 · *Verified by:* test to be defined
- **REQ-RESP-03** · WHEN a flagged request's path is `/maze/{id}` with a valid id, THE SYSTEM SHALL route it through the normal maze handler; it SHALL NOT run detection twice or compose two maze responses.
  *Source:* INTEG + REQ-RESP-02 interaction · *Verified by:* test to be defined
- **REQ-RESP-04** · THE SYSTEM SHALL preserve the normal `GET /robots.txt` response for every client, including flagged ones. `/robots.txt` is never replaced with maze content.
  *Source:* Threat T2 ("hiding real `/robots.txt` from well-behaved crawlers would damage trust") + REQ-DET-01 exemption · *Verified by:* threat T2 + test to be defined
- **REQ-RESP-05** · THE SYSTEM SHALL apply the v1 2–5s delay to any flagged response that lands at a `/maze/*` path (via REQ-RESP-03). Flagged responses at non-maze paths (e.g. `/` served as maze content) SHALL also be delayed under the same bounds, so the client experience is uniform.
  *Source:* v1 REQ-DELAY-01/02 extended by Q3b · *Verified by:* test to be defined

### DLOG — Detection log

- **REQ-DLOG-01** · WHEN a request is flagged and routed into the maze by detection, THE SYSTEM SHALL append exactly one JSON object (one line, newline-terminated) to the detection log containing: `ts` (ISO-8601 UTC with `Z`), `ip` (string), `ua` (string or `null`), `path` (string), `triggers` (list of signal id strings as defined in REQ-DET-02..05).
  *Source:* Done-when #5 (post-user-revision: route-into-maze only) · *Verified by:* test to be defined
- **REQ-DLOG-02** · THE SYSTEM SHALL NOT write a detection-log entry for requests that pass detection without being flagged.
  *Source:* Done-when #5 (post-user-revision) · *Verified by:* test to be defined
- **REQ-DLOG-03** · THE SYSTEM SHALL write the detection log to a configurable path, defaulting to `logs/detection.log`, append-only, with no rotation in v2.1.
  *Source:* "same shape family as v1 access log" (Done-when #5) + v1 LOG constraints · *Verified by:* test to be defined
- **REQ-DLOG-04** · THE SYSTEM SHALL NOT write request bodies, query strings, cookies, or arbitrary headers beyond the User-Agent to the detection log.
  *Source:* CLAUDE.md security policy, mirrors v1 REQ-LOG-03 · *Verified by:* test to be defined

### SWITCH — Kill switch

- **REQ-SWITCH-01** · THE SYSTEM SHALL expose a boolean setting (env var `TARPIT_DETECTION_ENABLED`, default `true`) that, when `false`, disables all detection: every request is routed to its normal handler as if v2.1 were not installed.
  *Source:* Done-when #6 · *Verified by:* test to be defined
- **REQ-SWITCH-02** · WHILE detection is disabled via REQ-SWITCH-01, THE SYSTEM SHALL NOT write any entry to the detection log, SHALL NOT update per-IP state, and SHALL serve `/` with its v1 bait-link content.
  *Source:* Done-when #6 + Q5b (bait retained) · *Verified by:* test to be defined

### INTEG — v1 integration

- **REQ-INTEG-01** · THE SYSTEM SHALL preserve the v1 contracts for `GET /robots.txt`, `GET /`, and `GET /maze/{id}` (as frozen in `specs/tar-pit/design.md §2.1`) exactly, for every non-flagged request.
  *Source:* "Constraints inherited from v1" (source spec) · *Verified by:* test to be defined
- **REQ-INTEG-02** · THE SYSTEM SHALL retain the v1 hidden bait anchor on `/` unchanged. A non-flagged client that follows the bait link reaches `/maze/root` and from that point is subject to normal v1 maze behavior.
  *Source:* Q5b (detection subsumes bait but does not remove it) · *Verified by:* test to be defined
- **REQ-INTEG-03** · THE SYSTEM SHALL continue to write to the v1 access log (`logs/access.log`) for every maze response, including flagged responses routed to maze content. The detection log is additive; it does not replace the access log.
  *Source:* v1 REQ-LOG-01 + v2.1 DLOG · *Verified by:* test to be defined

### SEC — Threats introduced by v2.1

- **REQ-SEC-01** · WHILE the per-IP state table is at or near its configured maximum, incoming requests from new source IPs SHALL evict least-recently-seen entries rather than fail or grow unbounded. (Addresses **T1** below.)
  *Source:* Threat T1 · *Verified by:* threat T1
- **REQ-SEC-02** · THE SYSTEM SHALL NOT trust any client-supplied header (e.g. `X-Forwarded-For`, `X-Real-IP`) for source-IP identification in v2.1. (Addresses **T3** below.)
  *Source:* Threat T3 + REQ-STATE-04 · *Verified by:* threat T3

**Threats (local to this spec):**
- **T1 — Unbounded state table.** A hostile client fans out across many source IPs; without a cap, in-memory state grows until OOM. Mitigated by REQ-STATE-03 + REQ-SEC-01.
- **T2 — Hiding `/robots.txt` from compliant crawlers.** If detection served maze content at `/robots.txt`, well-behaved crawlers (Googlebot, archive.org) would never see the real disallow rules, reversing the point of the project. Mitigated by REQ-RESP-04.
- **T3 — IP spoofing via trusted headers.** If v2.1 parsed `X-Forwarded-For`, a direct client could spoof its identity and either exhaust state or dodge rate limits. Mitigated by REQ-SEC-02 (deliberately defer until public-deployment spec handles proxy trust properly).

## 4. Non-functional requirements

- **Latency**: the detection middleware SHALL add less than 1 ms of overhead to the non-flagged request path, measured as median latency delta between detection-enabled and detection-disabled runs on the same hardware. *Source:* implied by "normal browser making normal pacing requests continues to see `/`" (Done-when #4) — detection must be invisible to real users.
- **Memory**: with the per-IP state table at its configured maximum, resident memory SHALL stay within a bound that the design will fix. *Source:* NFR check against T1.
- **Observability**: the detection log SHALL be the single source of truth for "which clients were routed into the maze and why". No parallel reporting mechanisms. *Source:* Done-when #5.
- **Simplicity**: v2.1 SHALL NOT add runtime dependencies beyond those already in `pyproject.toml`. Pure stdlib for state tracking; no Redis, no SQLite, no new HTTP clients. *Source:* implied by Q1a + CLAUDE.md "simplest readable solution wins".

## 5. Constraints

- Python 3.13, FastAPI, uvicorn, `uv` for dep management. *Source:* `specs/tar-pit/design.md` + CLAUDE.md.
- Code lives under `src/tarpit/` using the same layout conventions as v1. *Source:* "Constraints inherited from v1".
- All logs are JSON lines, one object per line, UTF-8. *Source:* "Constraints inherited from v1".
- `GET /robots.txt`, `GET /`, `GET /maze/{id}` keep their v1 response contracts for non-flagged clients. *Source:* "no breaking changes to v1".
- Per-IP state is **in-memory only**, retained for the process lifetime, discarded on restart. *Source:* Q1a.
- Detection model is **hard rules, OR-composed**. *Source:* Q2a.
- Flagged response: **HTTP 200 with maze HTML at the original path**, no redirect. *Source:* Q3b.
- No false-positive recovery mechanism in v2.1. *Source:* Q4a.
- v1 bait anchor on `/` is retained unchanged; detection is additive. *Source:* Q5b.
- Forged-UA criteria are limited to: missing/empty UA, OR UA (lowercased) containing `bot` / `crawler` / `spider`. *Source:* Q6b.
- Source IP for state keying comes from `request.client.host` only. *Source:* REQ-STATE-04 + REQ-SEC-02.
- **Rate budget**: **60 requests per 60-second sliding window** per source IP across all detection-eligible paths. *Source:* Q-OPEN-2 resolved. Chosen as the cheapest value that is both well above normal-browser pacing on this three-endpoint service and tight enough to catch aggressive scrapers.
- **No rate-trip warm-up.** A new source IP is subject to the full budget immediately; the budget alone is roomy enough that normal browsing cannot trip it on this service's current surface. *Source:* Q-OPEN-4 resolved.
- **State table cap**: **10,000 entries**, LRU eviction. Target resident memory ≈ 10 MB. *Source:* Q-OPEN-3 resolved.
- **Signal id vocabulary (frozen):** `ua_missing`, `ua_known_bot_pattern`, `rate_exceeded`, `prior_maze_visit`. Used in the DLOG `triggers` field. *Source:* Q-OPEN-6 resolved.
- **Legitimate-crawler carve-out: deliberately not applied in v2.1.** REQ-DET-03 will flag Googlebot / Bingbot / archive.org and route them into maze content at `/`. Acceptable only because v2.1 is local-only. **MUST be resolved before public deployment (v2 item #1)** — otherwise the service loses discoverability. *Source:* Q-OPEN-1 resolved (deferred).
- **Trusted-header IP extraction: not in v2.1.** `request.client.host` is correct for direct connections but will see the proxy IP for every request behind nginx/Caddy. **MUST be resolved before public deployment (v2 item #1)** or detection becomes useless. *Source:* Q-OPEN-5 resolved (deferred).

## 6. Open questions

None open — all pre-design questions (Q-OPEN-1 legitimate-crawler carve-out, Q-OPEN-2 rate budget numbers, Q-OPEN-3 state table cap, Q-OPEN-4 rate-trip warm-up, Q-OPEN-5 reverse-proxy IP extraction, Q-OPEN-6 signal-id vocabulary) resolved and recorded as constraints.

Two items are **deferred to the v2-item-#1 public-deployment spec** and are hard prerequisites for that spec (not for v2.1 design):
- Legitimate-crawler carve-out (Q-OPEN-1) — v2.1 keeps the aggressive UA rule.
- Trusted-header IP extraction (Q-OPEN-5) — v2.1 reads `request.client.host` only.
