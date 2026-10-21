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
