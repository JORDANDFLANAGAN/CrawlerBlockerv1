# Tar Pit: Spec

Oct 21, 2026 · @Jordan

A small FastAPI service that traps crawlers ignoring `robots.txt` in an endless, slow maze of generated pages. Local only until the logic is validated.

## How it works

Four pieces. Only crawlers that break the rule ever see the maze.

1. **Rule**: `robots.txt` says `Disallow: /maze/`. Anyone who enters is breaking it.
2. **Bait**: a hidden link on a real page points into `/maze/`. Humans can't see it; naive crawlers follow it.
3. **Maze**: every `/maze/<id>` page returns generated text plus 5-10 links to other `/maze/<id>` pages. It never ends.
4. **Slowness**: each response is delayed 2-5 seconds, tying up the crawler's connections.

**Key decision: deterministic pages.** The page ID is the random seed, so the same URL always gives the same page. No database, no stored pages, and the maze looks like a stable real site.

## Request flow

One request to `GET /maze/a8f3`:

1. Seed the random generator with `a8f3`.
2. Generate \~200 words with the Markov chain.
3. Generate 5-10 new IDs from the same seed; render them as links to `/maze/<new id>`.
4. Log the request.
5. Sleep a random 2-5 seconds (async, so it costs the server almost nothing).
6. Return the HTML page.

Because step 1 fixes the seed, repeating the URL repeats the page exactly.

## Markov chain

The text generator. Locally readable, globally meaningless, which is the point. Trained once on *Moby-Dick* (public domain, varied vocabulary).

- **Training**: read the book once. For every pair of consecutive words, record which word came next. Example: `"the whale"` is followed by `["was", "swam", "was", ...]`.
- **Generating**: start from a random pair, pick a next word from that pair's list, slide the window forward one word, repeat.
- **Order 2** (a 2-word window). Order 1 produces gibberish; order 3+ copies the book too closely.
- **Seeded**: use `random.Random(seed)` per request, never the global generator, so output is reproducible.

## Files, endpoints and log

```
tarpit/
  main.py        # FastAPI routes + logging
  markov.py      # train + generate
  corpus.txt     # Moby-Dick plain text
  access.log     # JSON lines
```

| Endpoint | Returns |
| --- | --- |
| `GET /robots.txt` | `Disallow: /maze/` |
| `GET /` | A tiny real page containing the hidden link |
| `GET /maze/{id}` | Generated text + 5-10 links, after a 2-5s delay |

One JSON object per line in `access.log`, easy to analyze later:

```json
{"ts": "2026-10-21T10:40:12Z", "ip": "203.0.113.7", "ua": "SomeBot/1.0", "path": "/maze/a8f3", "delay_s": 3.4}
```

## Done when

v1 is finished when all of these pass locally:

- [ ] `/robots.txt` disallows `/maze/`
- [ ] A `curl` loop that follows links never hits a dead end
- [ ] Fetching the same URL twice returns identical content
- [ ] Responses are visibly delayed (2-5s, varying)
- [ ] The log records IP, user agent, path and time as JSON lines

**Out of scope for v1:** bot detection or fingerprinting, tuning for training-data pollution, rate limiting, reverse proxy setup (nginx/Caddy), public deployment. Revisit after local validation.

## Scope for v2

Only after v1 is validated locally. Roughly in this order:

1. **Public deployment**: run behind a reverse proxy (nginx or Caddy) on a cheap server, so real bots find it.
2. **Bot detection**: route only suspected bots into the maze, using user agent, request rate and whether they ignored `robots.txt`.
3. **Rate limiting and abuse protection**: cap concurrent maze connections so the tar pit can't be turned against you.
4. **Log analysis**: a small script to summarize who got trapped, how deep they went and how long they stayed.
5. **Better content**: test whether more convincing generated text keeps crawlers inside longer.

Not planned: any claim that this degrades AI models. That effect is unproven; the goal is wasting crawler time and learning who ignores the rules.
