# Tar Pit

A three-layer defence against unwanted automated traffic. Polite crawlers are kept out by `robots.txt`. Impolite ones get wasted — pulled into an endless maze of slow, machine-generated pages. Clients that flood the service too hard get a cheap `429 Too Many Requests` instead, so they can't tie the server up.

## What it achieves

- **Honest crawlers (Googlebot, etc.) are unaffected.** They read `robots.txt`, see `/maze/` is disallowed, and move on. The site stays indexable.
- **Scrapers that ignore `robots.txt` get stuck.** Every page they fetch has 5–10 hidden links to further pages. Every page takes 2–5 seconds to serve. There is no exit. Their crawl budget drains while they get only auto-generated nonsense.
- **High-rate attackers get denied cheaply.** An IP that opens too many simultaneous connections or sends too many requests gets a 1–2 ms `429` — no delay, no content generation, no CPU spent. The expensive tarpit is reserved for traffic that actually warrants wasting.
- **The two defences cooperate.** When abuse protection denies a client, it flags that IP so follow-up requests from the same client — once they slow down — fall into the maze instead of just getting more 429s. Denial first, deception second.

## How it achieves it

Three independent layers sit in front of the application, in this order:

1. **Abuse protection (v2.2)** — cheap first. Counts requests per IP and globally; caps concurrent in-flight requests per IP and globally. First limit breached → `429`, done in milliseconds.
2. **Detection (v2.1)** — if abuse protection let the request through, look for bot signals: missing `User-Agent`, obvious bot keywords in the UA, high per-IP rate, or a prior visit to `/maze/`. Any hit → route the request into maze content, even if it was asking for `/`.
3. **Tar pit (v1)** — the actual trap. For any `/maze/{id}` request, generate 2–5 s of synthetic HTML seeded by the id (same id = same page, deterministic). The HTML contains 5–10 fresh outgoing `/maze/*` links. There is no termination condition.

A single-process FastAPI service runs all three. All state is in-memory — no database, no Redis — which is why `workers=1` is enforced.

## Install

```sh
uv sync
```

## Run

```sh
uv run tarpit
```

This starts the service on `127.0.0.1:8000` with a **single uvicorn worker** (the default). Do **not** raise `--workers` in v1 — the append-only access log is not safe under multiple workers.

## Demo

A ~30-second walk-through that shows all three layers working. With the server running, open a second terminal and run each block in order.

**1. Baseline — the three endpoints work.**

```sh
curl -s http://127.0.0.1:8000/robots.txt
# User-agent: *
# Disallow: /maze/

curl -s -o /dev/null -w "HTTP %{http_code}  bytes=%{size_download}\n" http://127.0.0.1:8000/
# HTTP 200  bytes=226         (root bait page)

curl -s -o /dev/null -w "HTTP %{http_code}  bytes=%{size_download}  time=%{time_total}s\n" http://127.0.0.1:8000/maze/deadbeef
# HTTP 200  bytes≈1800  time≈3.5s   (2–5s delay, Markov content)
```

**2. Trip the per-IP concurrency cap.** Default `TARPIT_CC_PER_IP_MAX=10`. Fire 15 concurrent `/maze/*` requests — expect exactly 10× `200` (slow) and 5× `429` (instant).

```sh
for i in $(seq 1 15); do
  curl -s -o /dev/null -w "req=$i status=%{http_code} time=%{time_total}s\n" \
    "http://127.0.0.1:8000/maze/abc${i}" &
done
wait
```

Expected (order will vary):

```
req=11 status=429 time=0.001s      ← abuse protection, instant
req=12 status=429 time=0.001s
req=13 status=429 time=0.002s
req=14 status=429 time=0.001s
req=15 status=429 time=0.001s
req=4  status=200 time=2.28s       ← passed; paid the 2–5s delay
req=8  status=200 time=2.30s
req=1  status=200 time=2.61s
...
```

**3. Inspect `logs/abuse.log` — the 429s were recorded.**

```sh
cat logs/abuse.log
# {"ts":"2026-10-23T14:07:01Z","ip":"127.0.0.1","ua":"curl/8.7.1","path":"/maze/abc11","reason":"per_ip_concurrency"}
# {"ts":"2026-10-23T14:07:01Z","ip":"127.0.0.1","ua":"curl/8.7.1","path":"/maze/abc12","reason":"per_ip_concurrency"}
# ... (5 lines total, one per 429)
```

**4. Signal flow: abuse → detection.** Your IP has now been flagged. The next request to `/` — a plain, non-maze path — should serve maze content instead of the normal 226-byte root page.

```sh
curl -s -o /dev/null -w "HTTP %{http_code}  bytes=%{size_download}  time=%{time_total}s\n" http://127.0.0.1:8000/
# HTTP 200  bytes≈1600  time≈4.7s   ← maze content served inline, not the root bait
```

Confirm in `logs/detection.log`:

```sh
tail -1 logs/detection.log
# {"ts":"...","ip":"127.0.0.1","ua":"curl/8.7.1","path":"/","triggers":["prior_maze_visit"]}
```

**5. `/robots.txt` is never limited.** Even after being flagged:

```sh
curl -s -o /dev/null -w "HTTP %{http_code}  time=%{time_total}s\n" http://127.0.0.1:8000/robots.txt
# HTTP 200  time=0.002s
```

**What you just saw:**
- Layer 1 denied the flood cheaply (429s in ~1 ms, no CPU spent on Markov).
- Layer 1 signalled Layer 2 one-way — same IP, follow-up request, different behaviour.
- Layer 2 served maze content inline on a non-maze path (deception, not denial).
- Layer 3 produced deterministic, link-filled HTML with a visible delay.
- Compliant-crawler contract (`/robots.txt`) was preserved throughout.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/robots.txt` | Disallows `/maze/` to compliant crawlers |
| `GET` | `/` | Bait page — visible human copy plus one hidden link to `/maze/root` |
| `GET` | `/maze/{id}` | Maze page — ~200 Markov words, 5–10 hidden outgoing links, 2–5 s delay |

## Access log

Requests to `/maze/{id}` are logged to `logs/access.log` relative to the working directory. The `logs/` directory is created automatically at startup if it does not exist.

Each line is a JSON object:

```json
{"ts": "2026-10-21T10:40:12Z", "ip": "127.0.0.1", "ua": "curl/8.0", "path": "/maze/a8f3", "delay_s": 3.42}
```

Fields: `ts` (ISO-8601 UTC), `ip`, `ua` (or `null`), `path`, `delay_s`.

## Configuration

Copy `.env.example` to `.env` and edit as needed:

```sh
cp .env.example .env
```

All settings use the `TARPIT_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `TARPIT_HOST` | `127.0.0.1` | Bind address |
| `TARPIT_PORT` | `8000` | Listen port |
| `TARPIT_RELOAD` | `false` | Hot-reload (dev only) |
| `TARPIT_LOG_LEVEL` | `info` | Uvicorn log level |
| `TARPIT_CORPUS_PATH` | `src/tarpit/corpus.txt` | Path to Markov corpus |
| `TARPIT_LOG_PATH` | `logs/access.log` | Access log path |
| `TARPIT_DELAY_MIN_S` | `2.0` | Minimum per-request delay (seconds) |
| `TARPIT_DELAY_MAX_S` | `5.0` | Maximum per-request delay (seconds) |

## Verify v1

Start the service in one terminal, then run these checks in another.

**robots.txt disallows /maze/:**

```sh
curl -s http://localhost:8000/robots.txt
# Expected:
# User-agent: *
# Disallow: /maze/
```

**curl loop following links never dead-ends:**

```sh
ID=root
for i in $(seq 1 10); do
  echo "Fetching /maze/$ID ..."
  NEXT=$(curl -s "http://localhost:8000/maze/$ID" | grep -oP 'href="/maze/\K[^"]+' | head -1)
  ID=$NEXT
done
echo "Still going — no dead end."
```

**Same URL returns identical body:**

```sh
curl -s http://localhost:8000/maze/a8f3 > /tmp/r1.html
curl -s http://localhost:8000/maze/a8f3 > /tmp/r2.html
diff /tmp/r1.html /tmp/r2.html && echo "Identical"
```

**Visible 2–5 s delay per /maze/ request:**

```sh
time curl -s http://localhost:8000/maze/a8f3 > /dev/null
# real time should be between 2s and 5s
```

**Access log is JSON lines:**

```sh
tail -5 logs/access.log | python3 -c "import sys, json; [json.loads(l) for l in sys.stdin]; print('All lines valid JSON')"
```

---

## Detection (v2.1)

v2.1 adds a detection layer that routes suspicious clients into the maze automatically — without needing them to follow the hidden bait link.

### Kill switch

`TARPIT_DETECTION_ENABLED` (default `true`) controls the entire detection subsystem. Set it to `false` for incident recovery without a code change:

```sh
TARPIT_DETECTION_ENABLED=false uv run tarpit
```

When `false`: every request is routed to its normal handler unchanged. No detection log is written. No per-IP state is updated. The v1 bait anchor on `/` remains in place.

### Detection settings

| Variable | Default | Description |
|----------|---------|-------------|
| `TARPIT_DETECTION_ENABLED` | `true` | Master kill switch for all detection |
| `TARPIT_DETECTION_LOG_PATH` | `logs/detection.log` | Path for the detection event log |
| `TARPIT_DETECTION_RATE_BUDGET` | `60` | Max requests per IP per window before flagging |
| `TARPIT_DETECTION_RATE_WINDOW_S` | `60.0` | Sliding window length in seconds |
| `TARPIT_DETECTION_STATE_MAX_ENTRIES` | `10000` | LRU cap on per-IP state table (entries) |

### Detection signals

A request is flagged if **any** of these signals fires:

| Signal id | Condition |
|-----------|-----------|
| `ua_missing` | `User-Agent` header is absent or empty |
| `ua_known_bot_pattern` | UA (case-insensitive) contains `bot`, `crawler`, or `spider` |
| `rate_exceeded` | Source IP made more than `TARPIT_DETECTION_RATE_BUDGET` requests in the last `TARPIT_DETECTION_RATE_WINDOW_S` seconds |
| `prior_maze_visit` | Source IP previously requested any `/maze/*` path in this process lifetime |

Flagged requests on non-maze paths (e.g. `/`) receive maze content at the requested path (HTTP 200, no redirect). Flagged requests on valid `/maze/{id}` paths are forwarded to the normal maze handler.

### Detection log

Flagged requests are logged to `logs/detection.log` (one JSON object per line). The log is written only when a request is actually routed into the maze — non-flagged traffic is never logged here.

Schema (see also [design.md §2.3](specs/bot-detection/design.md)):

```json
{"ts": "2026-10-21T12:40:00Z", "ip": "203.0.113.7", "ua": null, "path": "/", "triggers": ["ua_missing"]}
```

Fields: `ts` (ISO-8601 UTC), `ip`, `ua` (or `null`), `path`, `triggers` (list of signal ids).

### Monitoring both logs

```sh
tail -F logs/access.log logs/detection.log
```

### Single-worker requirement

**Detection state is per-process and in-memory.** `main.py` enforces `workers=1` in its uvicorn call. Raising `--workers` above 1 would give each worker its own isolated state table, defeating detection entirely — a bot rotating between workers would trip neither. **Do not change `workers=1` in v2.x.**

This constraint will be revisited when detection state is externalized (a v3+ concern).

---

## How it works (in plain Python)

Three mechanisms, each simpler than it looks. No external services, no new dependencies past FastAPI itself.

### Dependencies

The whole thing runs on four runtime dependencies plus the standard library:

| Dependency | Why it's here |
|---|---|
| `fastapi` | HTTP routing + middleware; the shape of the whole app |
| `uvicorn` | ASGI server that actually runs it |
| `pydantic-settings` | Reads `TARPIT_*` env vars into a typed `Settings` object |
| `jinja2` | Renders `/maze/{id}` HTML from a template |

Everything else — the sliding windows, the LRU state tables, the Markov chain, the async-locked file writers — is built from `collections.OrderedDict`, `collections.deque`, `asyncio.Lock`, `random.Random`, `time.monotonic()`, and `json`. The project is a demonstration that pure-stdlib data structures carry you a long way before you need Redis or a database.

### Layer 3 — the tar pit (`src/tarpit/routers/maze.py` + `markov.py` + `ids.py`)

**What it does.** Serves a page at `/maze/{id}` that looks like a real page but is generated on the fly, takes 2–5 s to deliver, and contains 5–10 fresh outgoing `/maze/*` links. Every link leads to another identical trap.

**How, in Python.**

1. A one-time startup step reads `corpus.txt` and builds a **Markov chain** — a dictionary mapping each word to the list of words that followed it in the corpus. ~30 lines of code; stored in `app.state.chain`.
2. On each `/maze/{id}` request, the `id` string is used to seed a `random.Random(id)`. Because the seed is deterministic, the same id produces the exact same page every time — crawler dedup logic can't help, because every id *looks* new.
3. That seeded `Random` drives:
   - The Markov walk (~200 words) → body text.
   - A loop that generates 5–10 fresh hex ids via `secrets.token_hex`-style derivation → outgoing `/maze/{new_id}` links sprinkled through the body.
4. Before responding, the handler `await asyncio.sleep(random.uniform(2.0, 5.0))` — the delay is what actually wastes the attacker's time; the Markov content just makes it feel like a real site worth crawling.
5. The response is a Jinja template with the generated text and links interpolated in.

That's the whole trap. There is no "/maze/exit" route. There is no end condition. A crawler that depth-first-walks this will still be walking next week.

### Layer 2 — bot detection (`src/tarpit/detection/`)

**What it does.** Decides whether an incoming request *looks like* a bot. If yes, forces the request into maze content, even if it asked for `/`.

**How, in Python.**

1. **FastAPI HTTP middleware.** `register_detection_middleware(app)` installs a function that FastAPI calls for every request before the router sees it. It takes the request, decides, and either lets it through or synthesises a different response.
2. **Four signals, in `signals.py`.** Pure functions, no state mutation:
   - `ua_missing` — `request.headers.get("user-agent")` is `None` or empty.
   - `ua_known_bot_pattern` — a cheap regex matches `bot|crawler|spider` case-insensitively.
   - `rate_exceeded` — ask the state store: has this IP sent more than N requests in the last W seconds?
   - `prior_maze_visit` — ask the state store: has this IP ever fetched `/maze/*`?
3. **State in `state.py`.** `DetectionStateStore` is a dict keyed by IP. Each entry holds a `collections.deque[float]` of request timestamps and a `has_visited_maze: bool`. Two operations do all the work:
   - `record_request(ip, now)` — append `now` to the deque; prune timestamps older than `now - window`; move this IP to the end of an `OrderedDict` to track LRU order; evict the oldest IP if the table exceeds its cap.
   - `is_rate_exceeded(ip, now)` — count remaining timestamps in the deque; compare to budget.
4. **Deterministic order.** In the middleware: record, then evaluate signals (pure read), then — only if the path was `/maze/*` — mark the IP as having visited the maze. The *after*-evaluation marking is deliberate: a legitimate first-time visitor to `/maze/root` isn't flagged for having visited the maze; only their *next* request is.
5. **On a flagged request:** write one line to `logs/detection.log`, then either forward the request if it's already heading to the maze, or synthesise maze content inline at whatever path they asked for (HTTP 200, no redirect — bots can see redirects).

The whole module is ~200 lines of Python. No locks are needed because `workers=1` + no `await` inside the state methods means every mutation is atomic by construction.

### Layer 1 — abuse protection (`src/tarpit/abuse/`)

**What it does.** Caps how much work the server will do for any one client, and how much work it will do in total. First limit tripped → `429` in milliseconds; no Markov, no delay, no HTML.

**How, in Python.**

1. **Another FastAPI middleware**, registered *after* detection in `create_app()`. FastAPI runs middleware in reverse registration order, so abuse protection runs *first* at request time — the cheap check wins. Order is covered by a test (`test_abuse_middleware_ordering.py`) so this can't silently regress.
2. **Four counters, one `AbuseStateStore`:**
   - Per-IP sliding-window **rate** — same `deque[float]` + prune pattern as detection, different window + budget.
   - Per-IP **in-flight concurrency** — a plain `int` per IP, incremented on request acceptance and decremented in a `finally` block when `await call_next(request)` returns (or raises).
   - Global sliding-window **rate** — one shared deque for the whole process.
   - Global **in-flight concurrency** — one shared `int`.
3. **Check order inside the middleware:**
   1. Record the request against the per-IP + global rate windows (always, even for requests about to be denied — otherwise a client stuck at the limit never escalates).
   2. If per-IP rate exceeded → 429 with reason `per_ip_rate`.
   3. Else if global rate exceeded → 429 with reason `global_rate`.
   4. Else try to acquire a per-IP + global in-flight slot. If the per-IP cap is already at max → 429 with reason `per_ip_concurrency`. If the global cap is at max → 429 with reason `global_inflight`.
   5. Otherwise: call the rest of the stack inside `try: … finally: store.release_slot(ip)` so the counters always decrement, including on exceptions.
4. **The 429 response is deliberately empty.** No `Retry-After` header (giving a bot the exact resume time defeats the point). `Content-Type: text/plain; charset=utf-8`, `Content-Length: 0`. A single JSON line goes into `logs/abuse.log` naming which limit tripped.
5. **One-way signal to detection.** On any 429, the middleware calls `detection_store.flag_for_detection(ip, now)` — a thin new method that marks the IP as "has visited the maze". The next request from that IP, if it falls back under the abuse-protection limits, will trip `prior_maze_visit` in Layer 2 and get maze content. Denial first; deception second. The signal is **one-way**: detection never writes back into abuse state, so the two kill switches (`TARPIT_ABUSE_PROTECTION_ENABLED`, `TARPIT_DETECTION_ENABLED`) stay independent.

### Request flow, end to end

```
                                   ┌─────────────────┐
      incoming HTTP request ──────▶│  abuse_middle-  │
                                   │     ware        │
                                   └────────┬────────┘
                                            │
                      429 (empty body) ◀────┤  limit tripped?
                      + log abuse line      │
                      + flag IP in          │
                        detection store     │ no
                                            ▼
                                   ┌─────────────────┐
                                   │ detection_mid-  │
                                   │    dleware      │
                                   └────────┬────────┘
                                            │
                   maze content served ◀────┤  bot signal fired?
                   at requested path        │
                   + log detection line     │ no
                                            ▼
                                   ┌─────────────────┐
                                   │     router      │
                                   │  /, /maze/{id}, │
                                   │   /robots.txt   │
                                   └─────────────────┘
```

### Why single-worker

All state — per-IP timestamps, in-flight counters, LRU tables, Markov chain, append-only log files — lives in process memory. With `workers=2`, each worker gets its own copy, and a bot bouncing between workers trips neither one's rate limit. The service enforces `workers=1` in `main.py`'s uvicorn call. Externalising state to Redis is the v3 concern; until then, don't change it.

---

## Architecture (for contributors)

### Code layout

```
src/tarpit/
├── main.py            # create_app(), lifespan, uvicorn entrypoint
├── settings.py        # pydantic-settings, TARPIT_ prefix
├── dependencies.py    # FastAPI Depends() wiring for app.state
├── markov.py          # MarkovChain — deterministic word generator
├── ids.py             # hex id generator for outgoing maze links
├── renderer.py        # HTML / robots.txt templates
├── access_log.py      # AccessLogWriter — async-locked JSONL append
├── corpus.txt         # Markov training text
├── routers/
│   ├── root.py        # GET / and GET /robots.txt
│   └── maze.py        # GET /maze/{id} + reusable generate_maze_response()
└── detection/
    ├── middleware.py  # the HTTP middleware; wires signals → action
    ├── signals.py     # evaluate_request() — pure signal evaluation
    ├── state.py       # DetectionStateStore — LRU per-IP table
    └── log.py         # DetectionLogWriter — async-locked JSONL append
```

Specs that drive the work live under `specs/bot-detection/` (`requirements.md` → `design.md` → `tasks.md` → implementation). `design.md` holds the frozen contracts; prefer updating a spec over patching code when a contract needs to change.

### Determinism

`/maze/{id}` is deterministic by design: the `id` seeds `random.Random`, which drives both the Markov walk and the choice of outgoing link ids. Same id in → byte-identical body out. This is what makes the "same URL returns identical body" verify step work, and it's what lets a crawler's deduplication *not* save it — every id looks like a fresh page.

### Request flow with detection enabled

For every request, `detection_middleware` runs before the router:

1. If `detection_enabled` is false, or path is `/robots.txt`, pass through unchanged.
2. Read client IP; take a `time.monotonic()` timestamp.
3. `store.record_request(ip, now)` — mutates the sliding-window counter.
4. `evaluate_request(request, store, now)` — pure read, returns the list of triggered signal ids in the order `[ua_missing | ua_known_bot_pattern, rate_exceeded, prior_maze_visit]`.
5. `store.mark_maze_visit(ip)` **if and only if** the path starts with `/maze/`. This happens *after* evaluation so the current request can't trip `prior_maze_visit` for itself — it only taints *future* requests from that IP.
6. If any signal fired: write one line to `detection.log`, then either forward to the router (maze paths) or synthesize maze content inline via `generate_maze_response(seed=BAIT_ID, …)` (non-maze paths, HTTP 200, no redirect).
7. Otherwise, pass through to the router.

The two writers (`AccessLogWriter`, `DetectionLogWriter`) each hold an `asyncio.Lock` around the file append, which is why the service is single-worker — the locks only serialize within one process.

### Testing

- `uv run pytest -q` runs the full suite. Tests mirror the source tree under `tests/`.
- Tests describe behavior, not implementation (see `CLAUDE.md`). If a test fails, fix the code — don't adjust the test unless the behavior being specified has intentionally changed.
- Every new route needs a test before it's considered done.

---

## Abuse protection (v2.2)

v2.2 adds a rate-limiting and concurrency-capping layer that runs **before** detection. Where detection answers "is this a bot?", abuse protection answers "how much work will we do for this client?".

Any request that exceeds a limit receives HTTP 429 (empty body). The limited IP is simultaneously flagged in the detection state so subsequent requests — if they fall back under the limit — enter the maze instead of getting a plain 429.

### Kill switch

`TARPIT_ABUSE_PROTECTION_ENABLED` (default `true`) controls the entire abuse-protection subsystem. The two kill switches are **independent**: disabling one does not affect the other.

```sh
# Disable abuse protection only (detection still runs)
TARPIT_ABUSE_PROTECTION_ENABLED=false uv run tarpit

# Disable detection only (abuse protection still runs)
TARPIT_DETECTION_ENABLED=false uv run tarpit
```

### Abuse-protection settings

| Variable | Default | Description |
|----------|---------|-------------|
| `TARPIT_ABUSE_PROTECTION_ENABLED` | `true` | Master kill switch for all abuse protection |
| `TARPIT_ABUSE_LOG_PATH` | `logs/abuse.log` | Path for the limit-breach event log |
| `TARPIT_RL_PER_IP_BUDGET` | `120` | Max requests per IP per window before 429 (2× the detection budget so detection flags first) |
| `TARPIT_RL_PER_IP_WINDOW_S` | `60.0` | Sliding window length in seconds for per-IP rate |
| `TARPIT_RL_STATE_MAX_ENTRIES` | `10000` | LRU cap on per-IP state table (entries) |
| `TARPIT_CC_PER_IP_MAX` | `10` | Max concurrent in-flight `/maze/*` requests per IP |
| `TARPIT_GL_INFLIGHT_MAX` | `500` | Max concurrent in-flight requests globally |
| `TARPIT_GL_RATE_BUDGET` | `1000` | Max requests globally per window before 429 |
| `TARPIT_GL_WINDOW_S` | `60.0` | Sliding window length in seconds for global rate |

### Limit-breach log

When any limit is breached, one JSON line is appended to `logs/abuse.log`:

```json
{"ts": "2026-10-23T14:00:00Z", "ip": "203.0.113.7", "ua": "BadBot/1.0", "path": "/maze/abc", "reason": "per_ip_rate"}
```

Fields: `ts` (ISO-8601 UTC), `ip`, `ua` (or `null`), `path`, `reason`.

The `reason` field records which limit was tripped first:

| Reason | Meaning |
|--------|---------|
| `per_ip_rate` | Source IP exceeded its per-IP request rate budget |
| `per_ip_concurrency` | Source IP has too many concurrent in-flight `/maze/*` requests |
| `global_inflight` | Total in-flight request count across all IPs reached the global cap |
| `global_rate` | Total request rate across all IPs exceeded the global budget |

Limits are evaluated in the order shown above; the first one that fires determines the reason.

Tail the log in real time alongside the detection log:

```sh
tail -F logs/abuse.log logs/detection.log
```

### Reverse-proxy caveat

> **Important before any public deployment.**

The service reads client IP from `request.client.host`. Behind a reverse proxy that does not forward trusted client-IP headers, every request appears to come from the **proxy's IP address**. In that configuration, per-IP caps (`TARPIT_RL_PER_IP_BUDGET`, `TARPIT_CC_PER_IP_MAX`) effectively become additional global caps — a single human user behind a shared proxy can trip them.

The default thresholds are tuned for a **local, single-user deployment** only. Before any public deployment:

1. Set up trusted-header IP extraction (a separate v2 item, not yet landed).
2. Re-tune all eight `TARPIT_*` thresholds for the expected traffic profile.
3. Test with realistic concurrency before going live.

Until step 1 lands, running this service behind a reverse proxy without trusted-header parsing is **unsupported**.
