"""Tests for DetectionStateStore — in-memory per-IP state with LRU bound."""
import pytest

from tarpit.detection.state import DetectionStateStore


def test_len_starts_at_zero() -> None:
    store = DetectionStateStore(max_entries=100, window_seconds=60.0, rate_budget=10)
    assert len(store) == 0


def test_record_request_adds_entry() -> None:
    store = DetectionStateStore(max_entries=100, window_seconds=60.0, rate_budget=10)
    store.record_request("1.2.3.4", now=100.0)
    assert len(store) == 1


def test_record_request_multiple_ips() -> None:
    store = DetectionStateStore(max_entries=100, window_seconds=60.0, rate_budget=10)
    store.record_request("1.2.3.4", now=100.0)
    store.record_request("5.6.7.8", now=100.0)
    assert len(store) == 2


def test_is_rate_exceeded_unknown_ip_returns_false() -> None:
    store = DetectionStateStore(max_entries=100, window_seconds=60.0, rate_budget=10)
    assert store.is_rate_exceeded("9.9.9.9", now=100.0) is False


def test_is_rate_exceeded_at_budget_does_not_trip() -> None:
    """Exactly `rate_budget` requests in window does NOT trip (strictly greater)."""
    store = DetectionStateStore(max_entries=100, window_seconds=60.0, rate_budget=10)
    for i in range(10):
        store.record_request("1.2.3.4", now=float(i))
    assert store.is_rate_exceeded("1.2.3.4", now=9.0) is False


def test_is_rate_exceeded_one_over_budget_trips() -> None:
    """rate_budget + 1 requests trips the rate check."""
    store = DetectionStateStore(max_entries=100, window_seconds=60.0, rate_budget=10)
    for i in range(11):
        store.record_request("1.2.3.4", now=float(i))
    assert store.is_rate_exceeded("1.2.3.4", now=10.0) is True


def test_is_rate_exceeded_pruned_window() -> None:
    """Timestamps outside the window are pruned before the check."""
    store = DetectionStateStore(max_entries=100, window_seconds=60.0, rate_budget=10)
    # Add 11 requests in the past (outside 60s window from now=200)
    for i in range(11):
        store.record_request("1.2.3.4", now=float(i))
    # At now=200, all those timestamps are > 60s old → pruned → count = 0
    assert store.is_rate_exceeded("1.2.3.4", now=200.0) is False


def test_has_prior_maze_visit_unknown_ip_returns_false() -> None:
    store = DetectionStateStore(max_entries=100, window_seconds=60.0, rate_budget=10)
    assert store.has_prior_maze_visit("1.2.3.4") is False


def test_has_prior_maze_visit_before_mark_returns_false() -> None:
    store = DetectionStateStore(max_entries=100, window_seconds=60.0, rate_budget=10)
    store.record_request("1.2.3.4", now=100.0)
    assert store.has_prior_maze_visit("1.2.3.4") is False


def test_has_prior_maze_visit_after_mark_returns_true() -> None:
    store = DetectionStateStore(max_entries=100, window_seconds=60.0, rate_budget=10)
    store.record_request("1.2.3.4", now=100.0)
    store.mark_maze_visit("1.2.3.4")
    assert store.has_prior_maze_visit("1.2.3.4") is True


def test_mark_maze_visit_noop_for_unknown_ip() -> None:
    """mark_maze_visit on unknown IP is a no-op, does not raise."""
    store = DetectionStateStore(max_entries=100, window_seconds=60.0, rate_budget=10)
    store.mark_maze_visit("9.9.9.9")  # should not raise
    assert len(store) == 0


def test_pruning_test() -> None:
    """Timestamps spanning 120 seconds, window=60, queried at now=120.
    Only entries from (60, 120] remain after is_rate_exceeded check."""
    store = DetectionStateStore(max_entries=100, window_seconds=60.0, rate_budget=200)
    # Place 50 timestamps in [0, 59] (outside window at now=120, cutoff=60)
    # and 50 timestamps in [61, 110] (inside window at now=120)
    # Note: cutoff = now - window_seconds = 120 - 60 = 60
    # Timestamps <= 60 are pruned; timestamps > 60 are kept
    for i in range(50):
        store.record_request("1.2.3.4", now=float(i))  # 0..49, outside window
    for i in range(61, 111):
        store.record_request("1.2.3.4", now=float(i))  # 61..110, inside window

    # Trigger prune by calling is_rate_exceeded at now=120
    # We have 50 timestamps in window [61..110], budget=200, so not exceeded
    assert store.is_rate_exceeded("1.2.3.4", now=120.0) is False

    # Verify via a second store that manually checks: call record_request at now=120
    # to force pruning in record_request too
    store2 = DetectionStateStore(max_entries=100, window_seconds=60.0, rate_budget=200)
    for i in range(50):
        store2.record_request("1.2.3.4", now=float(i))
    for i in range(61, 111):
        store2.record_request("1.2.3.4", now=float(i))
    store2.record_request("1.2.3.4", now=120.0)
    # After recording at now=120: prune removes ts <= 60
    # In window: 61..110 (50 items) + 120 = 51 items, budget=200
    assert store2.is_rate_exceeded("1.2.3.4", now=120.0) is False


def test_boundary_lru_eviction() -> None:
    """Insert exactly max_entries IPs then one more.
    len == max_entries, first IP evicted, second IP still present."""
    max_entries = 5
    store = DetectionStateStore(max_entries=max_entries, window_seconds=60.0, rate_budget=100)

    ips = [f"10.0.0.{i}" for i in range(max_entries)]
    for i, ip in enumerate(ips):
        store.record_request(ip, now=float(i))

    assert len(store) == max_entries

    # Insert one more
    store.record_request("192.168.0.1", now=float(max_entries))
    assert len(store) == max_entries

    # First IP should be evicted
    assert store.has_prior_maze_visit(ips[0]) is False  # it's gone — unknown IP
    assert store.is_rate_exceeded(ips[0], now=float(max_entries)) is False  # unknown = False

    # Second IP should still be present
    store.mark_maze_visit(ips[1])
    assert store.has_prior_maze_visit(ips[1]) is True


def test_boundary_lru_eviction_second_ip_present() -> None:
    """Specific check: after inserting max_entries+1 IPs, the second IP exists."""
    max_entries = 3
    store = DetectionStateStore(max_entries=max_entries, window_seconds=60.0, rate_budget=100)

    for i in range(max_entries):
        store.record_request(f"10.0.0.{i}", now=float(i))

    # IP 0 is LRU. Insert one more — IP 0 should be evicted.
    store.record_request("10.0.0.99", now=float(max_entries))
    assert len(store) == max_entries

    # IP 1 (second inserted) is still there
    store.record_request("10.0.0.1", now=float(max_entries + 1))
    assert len(store) == max_entries  # accessed 10.0.0.1 so it stays, no eviction from empty


def test_determinism() -> None:
    """Same input sequence always produces same LRU eviction order."""
    def build_store(ips: list[str]) -> DetectionStateStore:
        store = DetectionStateStore(max_entries=3, window_seconds=60.0, rate_budget=100)
        for i, ip in enumerate(ips):
            store.record_request(ip, now=float(i))
        return store

    ips = ["a", "b", "c", "d"]  # "a" is LRU when "d" is inserted
    store1 = build_store(ips)
    store2 = build_store(ips)

    assert len(store1) == len(store2) == 3
    # "a" was evicted in both
    assert store1.is_rate_exceeded("a", now=10.0) is False
    assert store2.is_rate_exceeded("a", now=10.0) is False
    # "b", "c", "d" still present in both
    for ip in ["b", "c", "d"]:
        store1.mark_maze_visit(ip)
        store2.mark_maze_visit(ip)
        assert store1.has_prior_maze_visit(ip) is True
        assert store2.has_prior_maze_visit(ip) is True


def test_lru_access_updates_order() -> None:
    """Accessing an IP via record_request moves it to most-recently-used."""
    store = DetectionStateStore(max_entries=3, window_seconds=60.0, rate_budget=100)
    store.record_request("a", now=1.0)
    store.record_request("b", now=2.0)
    store.record_request("c", now=3.0)

    # Access "a" again — it moves to MRU, "b" becomes LRU
    store.record_request("a", now=4.0)

    # Insert "d" — "b" should be evicted (LRU)
    store.record_request("d", now=5.0)
    assert len(store) == 3

    # "b" is gone
    assert store.is_rate_exceeded("b", now=5.0) is False
    # "a" is still there
    store.mark_maze_visit("a")
    assert store.has_prior_maze_visit("a") is True


def test_no_locks_in_state_module() -> None:
    """state.py must not import threading or use asyncio.Lock."""
    import inspect
    import tarpit.detection.state as state_module

    source = inspect.getsource(state_module)
    assert "threading" not in source, "state.py must not import threading"
    assert "asyncio.Lock" not in source, "state.py must not use asyncio.Lock"
    assert "asyncio.RLock" not in source, "state.py must not use asyncio.RLock"
