# Tar Pit v2.1: Bot Detection — Source Spec

Oct 21, 2026 · @Jordan · follow-on to [TarPit.md](./TarPit.md)

v1 traps anyone who both ignores `robots.txt` AND follows a `display:none` link. That's a strong signal but it only catches the subset of clients that take the bait. Lots of misbehaving crawlers would skip the hidden link (even naive ones vary) and hit `/`, `/robots.txt`, or guessed URLs directly. v1 has no answer for them — they see a normal site.

v2.1 adds a detection layer **in front of** v1. The maze stays untouched. Humans and well-behaved crawlers continue to see the normal `/` page. Clients that look like misbehaving bots get routed into the maze even if they didn't follow the hidden anchor.

## Problem

- Many ignore-`robots.txt` crawlers never trip the v1 trap because they parse HTML conservatively (skip `display:none`) or follow a different link-selection strategy.
- v1 has no observability on "who is scraping me and getting away with it" — the access log only records maze visitors.
- Shipping to public deployment without any detection means the trap catches almost nothing and the server pays bandwidth cost for serving real `/` pages to bots.

## What "detection" means here

The system observes signals it already has access to and uses them to decide, per request: "serve this client the normal page" or "redirect this client into the maze starting at `/maze/root` (or similar)." No new user-visible endpoints. No CAPTCHAs. No fingerprinting beyond what the HTTP layer already exposes.

Signals the source spec names:
- **User agent string** — presence/absence, known-bot patterns, obviously-forged UAs.
- **Request rate** per source IP — bursts, sustained throughput above a human threshold.
- **`robots.txt` compliance history** — has this client previously requested a `Disallow`'d path?

Other signals the design phase could consider but are not committed here: TLS fingerprint (JA3/JA4), header ordering, `Accept-*` header shape, source ASN, repeated same-URL requests.

## Not in scope for v2.1

- CAPTCHAs or any interactive challenge.
- IP reputation lookups against external services (requires network dependency, defer to a later v2.x).
- Rate limiting of maze connections (that's v2.2, separate feature).
- Rewriting the maze itself.
- Logging of non-maze traffic (today v1 only logs maze hits; whether v2.1 logs normal `/` traffic is a design question).

## Open shape (deliberately for `/spec-requirements` to clarify)

- **Where state lives.** Per-IP rate counters and prior-violation history need to persist across requests. In-memory works for a single process but dies on restart; something like SQLite or Redis survives but adds a dep. The right answer depends on how long "history" needs to live and how many sources we expect to track. Requirements should fix the retention window and tolerable restart-loss; design picks the storage.
- **Hard rules vs scoring.** Two valid shapes: (a) fixed rules — if any signal trips, maze; (b) weighted score with a threshold. (a) is easier to explain and test; (b) is more flexible but harder to tune without real traffic. No commitment yet.
- **Response for suspected bots.** Options: HTTP 302 redirect to `/maze/root`, 200 with maze content directly at the originally-requested path, or just serve the real page but log loudly. Different operational trade-offs.
- **How to recover.** A false positive (real user flagged as bot) today has no recovery path. Does v2.1 need one? Reset on next visit? Manual override?
- **Interaction with the hidden-link trap.** Does the v1 bait link remain? If yes, does hitting it still auto-route into the maze independent of detection, or does detection subsume that?

## Done when

v2.1 is finished when all of these are true in a public-deployment-ready state:

- A client with no UA / an obviously-forged UA ends up in the maze without touching the hidden link.
- A client exceeding the configured request-rate budget ends up in the maze on its next request.
- A client that previously requested a `Disallow`'d path is marked and ends up in the maze on its next visit (within the configured retention window).
- A normal browser making normal pacing requests continues to see `/` as plain HTML.
- When detection routes a client into the maze, the decision is logged as one JSON line (same shape family as the v1 access log) recording the triggering signals and the request path. Decisions that let a client through are **not** logged — operators review false positives from this log; false negatives are visible only indirectly, via the v1 maze access log being sparser than expected.
- A kill-switch config value disables detection entirely (serves everyone the normal page) for incident recovery.

## Constraints inherited from v1

- Python 3.13, FastAPI, `uv`.
- `src/tarpit/` package layout.
- Logging stays as JSON lines; any new log file follows the v1 schema conventions.
- No breaking changes to the three existing v1 endpoints' contracts (`GET /robots.txt`, `GET /`, `GET /maze/{id}`).
