"""Tests for evaluate_request signal evaluation."""
from fastapi import Request
from starlette.types import Scope

from tarpit.detection.signals import (
    SIGNAL_PRIOR_MAZE_VISIT,
    SIGNAL_RATE_EXCEEDED,
    SIGNAL_UA_KNOWN_BOT_PATTERN,
    SIGNAL_UA_MISSING,
    evaluate_request,
)
from tarpit.detection.state import DetectionStateStore


def make_request(
    ua: str | None = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    ip: str = "1.2.3.4",
    path: str = "/",
) -> Request:
    """Minimal Request stub — does not start an ASGI server."""
    headers: list[tuple[bytes, bytes]] = []
    if ua is not None:
        headers.append((b"user-agent", ua.encode()))
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": headers,
        "client": (ip, 12345),
        "app": None,
    }
    return Request(scope)


def make_store(
    budget: int = 100,
    window: float = 60.0,
    max_entries: int = 1000,
) -> DetectionStateStore:
    return DetectionStateStore(
        max_entries=max_entries,
        window_seconds=window,
        rate_budget=budget,
    )


# ——— UA signal tests ———

def test_ua_missing_header_fires_ua_missing() -> None:
    store = make_store()
    request = make_request(ua=None)
    triggers = evaluate_request(request, store, now=1.0)
    assert SIGNAL_UA_MISSING in triggers


def test_ua_empty_string_fires_ua_missing() -> None:
    store = make_store()
    request = make_request(ua="")
    triggers = evaluate_request(request, store, now=1.0)
    assert SIGNAL_UA_MISSING in triggers


def test_ua_missing_never_cooccurs_with_bot_pattern() -> None:
    """Mutual exclusion: ua_missing and ua_known_bot_pattern cannot both fire."""
    store = make_store()
    request = make_request(ua=None)
    triggers = evaluate_request(request, store, now=1.0)
    assert SIGNAL_UA_KNOWN_BOT_PATTERN not in triggers


def test_ua_empty_string_never_cooccurs_with_bot_pattern() -> None:
    store = make_store()
    request = make_request(ua="")
    triggers = evaluate_request(request, store, now=1.0)
    assert SIGNAL_UA_KNOWN_BOT_PATTERN not in triggers


def test_googlebot_fires_ua_known_bot_pattern() -> None:
    store = make_store()
    request = make_request(ua="Googlebot/2.1 (+http://www.google.com/bot.html)")
    triggers = evaluate_request(request, store, now=1.0)
    assert SIGNAL_UA_KNOWN_BOT_PATTERN in triggers


def test_crawler_ua_fires_ua_known_bot_pattern() -> None:
    store = make_store()
    request = make_request(ua="MyCrawler/1.0")
    triggers = evaluate_request(request, store, now=1.0)
    assert SIGNAL_UA_KNOWN_BOT_PATTERN in triggers


def test_spider_ua_fires_ua_known_bot_pattern() -> None:
    store = make_store()
    request = make_request(ua="WebSpider/2.3")
    triggers = evaluate_request(request, store, now=1.0)
    assert SIGNAL_UA_KNOWN_BOT_PATTERN in triggers


def test_normal_browser_ua_no_ua_signal() -> None:
    """Normal browser UA on first visit produces no triggers."""
    store = make_store()
    request = make_request(ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0")
    triggers = evaluate_request(request, store, now=1.0)
    assert SIGNAL_UA_MISSING not in triggers
    assert SIGNAL_UA_KNOWN_BOT_PATTERN not in triggers


def test_ua_case_insensitive_bot() -> None:
    """Pattern is IGNORECASE — uppercase BOT still fires."""
    store = make_store()
    request = make_request(ua="MyBOT/1.0")
    triggers = evaluate_request(request, store, now=1.0)
    assert SIGNAL_UA_KNOWN_BOT_PATTERN in triggers


# ——— Rate signal tests ———

def test_rate_exceeded_fires_when_over_budget() -> None:
    store = make_store(budget=5)
    ip = "1.2.3.4"
    # Record 6 requests to exceed budget of 5
    for i in range(6):
        store.record_request(ip, now=float(i))
    request = make_request(ip=ip)
    triggers = evaluate_request(request, store, now=5.0)
    assert SIGNAL_RATE_EXCEEDED in triggers


def test_rate_not_exceeded_at_budget() -> None:
    store = make_store(budget=5)
    ip = "1.2.3.4"
    for i in range(5):
        store.record_request(ip, now=float(i))
    request = make_request(ip=ip)
    triggers = evaluate_request(request, store, now=4.0)
    assert SIGNAL_RATE_EXCEEDED not in triggers


def test_rate_not_exceeded_for_unknown_ip() -> None:
    store = make_store(budget=5)
    request = make_request(ip="9.9.9.9")
    triggers = evaluate_request(request, store, now=1.0)
    assert SIGNAL_RATE_EXCEEDED not in triggers


# ——— Prior maze visit signal tests ———

def test_prior_maze_visit_fires_after_mark() -> None:
    store = make_store()
    ip = "1.2.3.4"
    store.record_request(ip, now=1.0)
    store.mark_maze_visit(ip)
    request = make_request(ip=ip)
    triggers = evaluate_request(request, store, now=2.0)
    assert SIGNAL_PRIOR_MAZE_VISIT in triggers


def test_prior_maze_visit_not_fired_before_mark() -> None:
    store = make_store()
    ip = "1.2.3.4"
    store.record_request(ip, now=1.0)
    request = make_request(ip=ip)
    triggers = evaluate_request(request, store, now=1.0)
    assert SIGNAL_PRIOR_MAZE_VISIT not in triggers


def test_prior_maze_visit_unknown_ip_false() -> None:
    store = make_store()
    request = make_request(ip="9.9.9.9")
    triggers = evaluate_request(request, store, now=1.0)
    assert SIGNAL_PRIOR_MAZE_VISIT not in triggers


# ——— Signal ordering and mutual exclusion ———

def test_trigger_order_all_signals_except_bot_pattern() -> None:
    """All three simultaneously-firing signals appear in deterministic order.
    ua_missing + rate_exceeded + prior_maze_visit.
    ua_known_bot_pattern cannot co-occur with ua_missing."""
    store = make_store(budget=5)
    ip = "1.2.3.4"
    # Exceed rate budget
    for i in range(6):
        store.record_request(ip, now=float(i))
    # Mark maze visit
    store.mark_maze_visit(ip)
    # Missing UA request
    request = make_request(ua=None, ip=ip)
    triggers = evaluate_request(request, store, now=5.0)
    assert triggers == ["ua_missing", "rate_exceeded", "prior_maze_visit"]


def test_ua_known_bot_pattern_with_rate_and_maze() -> None:
    """Bot UA + rate exceeded + prior maze visit returns in deterministic order."""
    store = make_store(budget=5)
    ip = "1.2.3.4"
    for i in range(6):
        store.record_request(ip, now=float(i))
    store.mark_maze_visit(ip)
    request = make_request(ua="Googlebot/2.1", ip=ip)
    triggers = evaluate_request(request, store, now=5.0)
    assert triggers == ["ua_known_bot_pattern", "rate_exceeded", "prior_maze_visit"]


def test_clean_request_no_triggers() -> None:
    """Normal browser, first visit, within budget → empty list."""
    store = make_store(budget=100)
    request = make_request(
        ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0",
        ip="1.2.3.4",
    )
    triggers = evaluate_request(request, store, now=1.0)
    assert triggers == []


# ——— IP source policy ———

def test_ip_from_client_host_no_header() -> None:
    """evaluate_request must use request.client.host; never X-Forwarded-For."""
    import inspect
    import tarpit.detection.signals as signals_module

    source = inspect.getsource(signals_module)
    assert "x-forwarded-for" not in source.lower(), \
        "signals.py must not read X-Forwarded-For"
    assert "x-real-ip" not in source.lower(), \
        "signals.py must not read X-Real-IP"


def test_evaluate_is_pure_no_store_mutation() -> None:
    """evaluate_request must not call record_request or mark_maze_visit."""
    import inspect
    import tarpit.detection.signals as signals_module

    source = inspect.getsource(signals_module)
    assert "record_request" not in source, \
        "signals.py must not call store.record_request"
    assert "mark_maze_visit" not in source, \
        "signals.py must not call store.mark_maze_visit"


def test_no_client_returns_unknown_ip() -> None:
    """If request.client is None, falls back to 'unknown' for IP lookup."""
    store = make_store(budget=5)
    # Seed the "unknown" IP entry
    store.record_request("unknown", now=1.0)
    store.mark_maze_visit("unknown")

    # Build request with no client
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [(b"user-agent", b"Mozilla/5.0")],
        "app": None,
    }
    request = Request(scope)
    # request.client is None by default when no "client" key in scope
    triggers = evaluate_request(request, store, now=2.0)
    assert SIGNAL_PRIOR_MAZE_VISIT in triggers
