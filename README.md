# Tar Pit v1

A crawler trap: compliant bots stay out via `robots.txt`; non-compliant ones enter an infinite maze of delayed pages.

## Install

```sh
uv sync
```

## Run

```sh
uv run tarpit
```

This starts the service on `127.0.0.1:8000` with a **single uvicorn worker** (the default). Do **not** raise `--workers` in v1 — the append-only access log is not safe under multiple workers.

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
