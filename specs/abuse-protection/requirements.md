# Abuse Protection (v2.2)

**Status:** Draft · **Owner:** jordanflanagan · **Date:** 2026-10-23 · **Source:** User problem statement (2026-10-22) + v2.1 deferred scope ([bot-detection/requirements.md §Non-goals](../bot-detection/requirements.md))

## 1. Problem

- The tar pit is designed to waste bot time via a 2–5s per-request delay. The same design means a sustained request stream from a bot ties up async worker slots (each held open for the full delay), burns CPU on Markov generation per response, and inflates `logs/access.log` indefinitely.
- v1 enforces `workers=1` and v2.1 reinforces it. A single process is therefore the only line of defence — there is no horizontal scaling to absorb load.
- v2.1's `rate_exceeded` detection signal (60 req / 60s per IP) flags **who** is a bot but imposes **no cap on work**. Once flagged, a bot still gets the full maze pipeline on every subsequent request.
- A distributed botnet in which each IP stays below v2.1's per-IP threshold can collectively saturate the service while triggering zero detection events — the entire v2.1 model collapses in this case.
- No current mechanism caps concurrent connections per IP, concurrent connections globally, or total request rate globally. All three are DoS-against-us vectors.

## 2. Goals & Non-goals

### Goals
- Cap per-IP concurrent connections and request rate on tar-pit endpoints so one aggressive client cannot monopolise the single worker.
- Cap global concurrent connections and request rate so a low-rate botnet cannot saturate the service by horizontal fan-out.
- Return HTTP 429 Too Many Requests when any limit is breached — standards-compliant, deliberately accepting the information leak that the client learns it was limited.
- Operate as a layer independent of v2.1 detection: separate state, separate thresholds, separate kill switch. Detection answers "is this a bot?"; abuse protection answers "how much work will we do for this client?".
- Expose every threshold as a `TARPIT_`-prefixed setting with safe defaults; expose a master kill switch mirroring v2.1's `TARPIT_DETECTION_ENABLED`.
- Remain pure-stdlib and single-worker in-memory, consistent with v2.1.

### Non-goals
- Shared state across workers (Redis / SQLite / any out-of-process store). Deferred to v3.
- Horizontal scaling via `workers>1`. Still forbidden; this spec reinforces the constraint.
- Fingerprinting beyond IP (TLS JA3, header-order hashing, behavioural clustering) as a botnet mitigation. Acknowledged weakness — out of scope.
- External IP reputation lookups (Spamhaus, AbuseIPDB). Still a later v2.x item.
- Trusted-header IP extraction (`X-Forwarded-For`). Still blocked on v2 item #1 (public deployment).
- Modifying, extending, or removing v2.1's `rate_exceeded` detection signal. v2.2 runs alongside it.
- Response-body size caps, per-request CPU-time caps, or token-bucket shaping of maze delay. If needed, separate feature.
- Observability beyond a simple limit-breach log line (metrics, Prometheus, dashboards). Future concern.
- Blocklisting / cool-down periods / sticky bans. A limited client is 429'd only for the duration of the breach window; no persistent penalty.

## 3. Functional requirements

### RL — Per-IP rate limiting

- **REQ-RL-01** · THE SYSTEM SHALL maintain a per-IP sliding-window request counter for abuse-protection purposes, distinct from v2.1's `DetectionStateStore`.
- **REQ-RL-02** · WHEN a source IP exceeds its configured per-IP request rate within the configured per-IP window, THE SYSTEM SHALL respond with HTTP 429 to every subsequent request from that IP until the window drains below the limit.
- **REQ-RL-03** · THE SYSTEM SHALL expose the per-IP rate budget as `TARPIT_RL_PER_IP_BUDGET` (positive integer, default `120`) and the per-IP window length as `TARPIT_RL_PER_IP_WINDOW_S` (positive float seconds, default `60.0`). The budget is deliberately set to 2× v2.1's `TARPIT_DETECTION_RATE_BUDGET` default so detection fires first.
  *Source:* Q4a (keep in-memory, workers=1); Q-RESOLVED-4 (co-operate with v2.1) · *Verified by:* test to be defined
- **REQ-RL-04** · THE SYSTEM SHALL bound per-IP state memory via an LRU cap, exposed as `TARPIT_RL_STATE_MAX_ENTRIES` (positive integer, default `10000`).
  *Source:* v2.1 LRU-cap pattern (REQ-STATE-01) · *Verified by:* test to be defined
- **REQ-RL-05** · THE SYSTEM SHALL count every incoming request (including those that will be denied with 429) against the per-IP rate budget. A denied request SHALL NOT be free; this prevents a client stuck at the limit from retrying indefinitely without further escalation.
  *Source:* Q-RESOLVED-6 · *Verified by:* test to be defined

### CC — Per-IP concurrency caps

- **REQ-CC-01** · THE SYSTEM SHALL maintain a per-IP counter of in-flight requests to `/maze/*`.
- **REQ-CC-02** · WHEN an IP's in-flight `/maze/*` request count equals or exceeds `TARPIT_CC_PER_IP_MAX` (positive integer, default `10`), THE SYSTEM SHALL respond with HTTP 429 to further `/maze/*` requests from that IP until an in-flight request completes. The default reflects the 2–5s maze delay: 10 concurrent slots sustain ~2–5 req/s per IP, well above any plausible single-human rate.
- **REQ-CC-03** · THE SYSTEM SHALL decrement the per-IP in-flight counter on request completion regardless of status code or exception.
  *Verified by:* test to be defined

### GL — Global caps (botnet mitigation)

- **REQ-GL-01** · THE SYSTEM SHALL maintain a global in-flight counter across all source IPs.
- **REQ-GL-02** · WHEN the global in-flight counter equals or exceeds `TARPIT_GL_INFLIGHT_MAX` (positive integer, default `500`), THE SYSTEM SHALL respond with HTTP 429 to any new tar-pit request until the counter drops below the limit. Uvicorn handles thousands of async slots comfortably; 500 leaves headroom while bounding worst-case memory and log growth.
- **REQ-GL-03** · THE SYSTEM SHALL maintain a global sliding-window request counter and SHALL respond with HTTP 429 when the global rate exceeds `TARPIT_GL_RATE_BUDGET` (positive integer, default `1000`) within `TARPIT_GL_WINDOW_S` (positive float seconds, default `60.0`). This equates to a sustained ~16 req/s across all sources and is the primary mitigation for distributed/botnet traffic (REQ-SEC scope).
- **REQ-GL-04** · THE SYSTEM SHALL NOT apply global caps to `GET /robots.txt`. (The compliant-crawler contract requires `robots.txt` to always be served.)
  *Source:* v1 REQ-ROBOTS-01 (`/robots.txt` is public) · *Verified by:* test to be defined

### SCOPE — Which paths are limited

- **REQ-SCOPE-01** · THE SYSTEM SHALL apply all abuse-protection checks to `/` and `/maze/*`.
- **REQ-SCOPE-02** · THE SYSTEM SHALL NOT apply abuse-protection checks to `/robots.txt`.
- **REQ-SCOPE-03** · THE SYSTEM SHALL apply abuse-protection checks **before** v2.1 detection runs — a request 429'd here never reaches the detection middleware. Reasoning: a 429 is near-free; running the maze pipeline costs 2–5s + CPU. Order the cheap check first. The side-effect (detection never sees 429'd IPs directly) is addressed by REQ-SIG-01's one-way signal flow.
  *Source:* Q3a (separate layer); Q-RESOLVED-5 · *Verified by:* test to be defined

### SIG — Signal flow between abuse protection and v2.1 detection

- **REQ-SIG-01** · WHEN abuse protection responds with HTTP 429 for any source IP, THE SYSTEM SHALL mark that IP in `DetectionStateStore` such that subsequent requests from that IP trigger the v2.1 detection middleware (serving maze content inline instead of 429).
  *Source:* Q-RESOLVED-3 · *Verified by:* test to be defined
- **REQ-SIG-02** · The signal flow SHALL be one-way: abuse protection may update detection state, but detection SHALL NOT update abuse-protection state. This preserves independent kill switches — toggling detection off does not disable abuse protection, and vice versa.
- **REQ-SIG-03** · THE SYSTEM SHALL document a recommended threshold relationship: `TARPIT_DETECTION_RATE_BUDGET` (v2.1) < `TARPIT_RL_PER_IP_BUDGET` (v2.2) so detection fires first on a chatty client (serving maze = deception) and abuse protection fires second on a sustained flooder (serving 429 = denial). This is a documentation requirement; the system SHALL NOT enforce the inequality at startup.
  *Source:* Q-RESOLVED-4
- **REQ-SIG-04** · WHEN `TARPIT_DETECTION_ENABLED` is `false`, abuse protection's signal to detection (REQ-SIG-01) SHALL be a no-op. Abuse protection continues to 429 breaches; it simply has nowhere to deposit the signal. This preserves the invariant that disabling detection fully removes it from the request path.

### RESP — Response shape

- **REQ-RESP-01** · THE SYSTEM SHALL return HTTP 429 with a stable, minimal body (plain text or empty) for all limit breaches.
- **REQ-RESP-02** · THE SYSTEM SHALL NOT include a `Retry-After` header in v2.2. (See Open questions.)
- **REQ-RESP-03** · THE SYSTEM SHALL NOT include stack traces, internal state, or threshold values in the 429 response. (CLAUDE.md "Don't echo errors verbatim to clients.")

### SWITCH — Kill switch

- **REQ-SWITCH-01** · THE SYSTEM SHALL expose `TARPIT_ABUSE_PROTECTION_ENABLED` (boolean, default `true`). WHEN `false`, THE SYSTEM SHALL skip all abuse-protection logic: no state mutation, no 429 responses, no abuse-log writes.
  *Source:* Mirrors v2.1 REQ-SWITCH-01 · *Verified by:* test to be defined
- **REQ-SWITCH-02** · WHILE abuse protection is disabled, v2.1 detection SHALL operate exactly as it does today, unaffected.

### LOG — Abuse log

- **REQ-LOG-01** · WHEN a request is 429'd by abuse protection, THE SYSTEM SHALL append one JSON object per line to `logs/abuse.log` (path configurable via `TARPIT_ABUSE_LOG_PATH`, default `logs/abuse.log`).
- **REQ-LOG-02** · The abuse log line SHALL contain `ts` (ISO-8601 UTC), `ip`, `ua` (or `null`), `path`, and `reason` (which limit tripped: one of `per_ip_rate`, `per_ip_concurrency`, `global_inflight`, `global_rate`).
- **REQ-LOG-03** · The abuse log SHALL be append-only with no rotation in v2.2.
- **REQ-LOG-04** · Non-429 responses SHALL NOT be written to the abuse log.
  *Source:* Mirrors v2.1 detection-log contract · *Verified by:* test to be defined

### SEC — Threats introduced by v2.2

- **REQ-SEC-01** · The 429 response leaks that a limit was tripped. v2.2 SHALL accept this trade-off; the standards-compliant status code is preferred over obscuring the throttle (Q2 answer).
- **REQ-SEC-02** · THE SYSTEM SHALL NOT trust client-supplied headers for source-IP identification. Same posture as v2.1 REQ-SEC-02 — IP comes from `request.client.host`.
- **REQ-SEC-03** · THE SYSTEM SHALL cap per-IP state via LRU (REQ-RL-04). A client cycling synthetic IPs can still evict legitimate records; mitigation is the global cap (REQ-GL-02, REQ-GL-03), not per-IP memory.
- **REQ-SEC-04** · Global caps SHALL have conservative defaults such that a legitimate single-user burst does not trip them. Specific values are a design-phase decision; the requirement is that defaults MUST be safe for a local-only deployment with a single human user.

## 4. Non-functional requirements

- **Performance**: abuse-protection checks SHALL add no measurable latency (<1ms per request on commodity hardware) to non-limited paths. The limiter is in-process memory lookups only.
- **Memory**: per-IP state SHALL be bounded by `TARPIT_RL_STATE_MAX_ENTRIES` (LRU). Global state is O(1).
- **Dependencies**: v2.2 SHALL NOT add runtime dependencies. Pure stdlib, consistent with v2.1 (CLAUDE.md "Simplest readable solution wins").
- **Observability**: a `tail -F logs/abuse.log` SHALL be sufficient to see what the limiter is doing in near-real-time, parallel to v2.1's `logs/detection.log`.
- **Testability**: every requirement above SHALL be covered by at least one test under `tests/`, using the real middleware stack (not mocks) per CLAUDE.md testing standards.

## 5. Constraints

- Python 3.13, FastAPI, managed by `uv`. No new runtime dependencies.
- `workers=1` remains locked. v2.2 reinforces this — the limiter's state is per-process in-memory and would silently fragment under multi-worker.
- Settings use `TARPIT_` prefix via `pydantic-settings` (`src/tarpit/settings.py`).
- v2.1 detection and v2.2 abuse protection MUST be independently togglable. Disabling one SHALL NOT affect the other.
- State is lost on process restart. There is no persistence requirement; a restart resets all counters, same posture as v2.1 (REQ-STATE-02).
- Trusted-header IP extraction is still deferred to v2 item #1. v2.2 reads `request.client.host` only — so behind a reverse proxy, every request appears to come from the proxy IP and global caps become the only effective layer. **This is a known gap and MUST be resolved by v2 item #1 before any public deployment.**

## 6. Open questions

The seven questions from the initial draft (Q-OPEN-1 … Q-OPEN-7) were resolved in review on 2026-10-23. For traceability, the resolutions are summarised below; the surviving open items follow.

### Resolved during review (2026-10-23)

- **Q-RESOLVED-1 · Threshold defaults.** Fixed inline on each requirement. Summary: `TARPIT_RL_PER_IP_BUDGET=120`, `TARPIT_RL_PER_IP_WINDOW_S=60.0`, `TARPIT_RL_STATE_MAX_ENTRIES=10000`, `TARPIT_CC_PER_IP_MAX=10`, `TARPIT_GL_INFLIGHT_MAX=500`, `TARPIT_GL_RATE_BUDGET=1000`, `TARPIT_GL_WINDOW_S=60.0`, `TARPIT_ABUSE_LOG_PATH=logs/abuse.log`. These are starting values for a local-only deployment; see Q-OPEN-1 (new) below.
- **Q-RESOLVED-2 · `Retry-After` header.** Resolved: no. REQ-RESP-02 excludes it unconditionally. Telling a bot exactly when to resume is counter to the tar-pit philosophy; the politeness-to-legitimate-clients argument is weak for traffic that was flagged as abusive in the first place.
- **Q-RESOLVED-3 · Abuse → detection signal.** Resolved: yes, one-way. See REQ-SIG-01, REQ-SIG-02. First breach → 429 (cheap denial); subsequent requests → maze (deception). The layers remain structurally independent — separate state, separate kill switches — but communicate via one explicit signal.
- **Q-RESOLVED-4 · Relationship with v2.1 `rate_exceeded`.** Resolved: both signals coexist with documented threshold ordering. See REQ-SIG-03. Operational intent: v2.1 flags the chatty client (serving maze); v2.2 denies the sustained flooder (serving 429). Default values enforce the inequality by construction (`60 < 120`).
- **Q-RESOLVED-5 · Middleware ordering.** Resolved: abuse protection runs before detection. See REQ-SCOPE-03. Reasoning: cheap check before expensive check; the side-effect on detection visibility is addressed by REQ-SIG-01.
- **Q-RESOLVED-6 · 429s count against the budget.** Resolved: yes. See REQ-RL-05. Free retries would let a client stuck at the limit never escalate; counting them preserves the sliding-window invariant.
- **Q-RESOLVED-7 · README structure.** Resolved: add a new "Abuse protection (v2.2)" section parallel to the existing "Detection (v2.1)" section. Keep the per-feature configuration tables separate rather than combining them — readers scan the README for one feature at a time.

### Remaining open

- **Q-OPEN-1 (new) · Threshold re-tuning for public deployment.** The eight defaults above are safe for a local, single-human-user deployment. When v2 item #1 (public deployment / reverse-proxy integration) lands, every per-IP threshold will need re-tuning because `request.client.host` will shift from "real client" to "proxy IP" until trusted-header parsing is in place. Design SHALL note this; actual re-tuning is deferred to the public-deployment spec.
- **Q-OPEN-2 (new) · Where exactly in the middleware stack does abuse protection sit?** REQ-SCOPE-03 fixes the order relative to detection ("before"), but FastAPI runs middleware in reverse registration order. Design must specify the registration call site in `create_app()` and confirm the resulting runtime order with a test. This is a mechanical design concern, not a scope one.
