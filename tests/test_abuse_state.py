"""Tests for AbuseStateStore — per-IP + global sliding-window rate limiting
and per-IP + global in-flight concurrency tracking."""
import pytest

from tarpit.abuse.state import AbuseStateStore
from tarpit.abuse.reasons import (
    REASON_PER_IP_CONCURRENCY,
    REASON_GLOBAL_INFLIGHT,
)


def make_store(
    per_ip_budget: int = 10,
    per_ip_window_s: float = 60.0,
    per_ip_concurrency_max: int = 5,
    per_ip_state_max_entries: int = 100,
    global_rate_budget: int = 100,
    global_window_s: float = 60.0,
    global_inflight_max: int = 50,
) -> AbuseStateStore:
    return AbuseStateStore(
        per_ip_window_s=per_ip_window_s,
        per_ip_budget=per_ip_budget,
        per_ip_concurrency_max=per_ip_concurrency_max,
        per_ip_state_max_entries=per_ip_state_max_entries,
        global_window_s=global_window_s,
        global_rate_budget=global_rate_budget,
        global_inflight_max=global_inflight_max,
    )


# --- record_request / rate ---

def test_len_starts_at_zero() -> None:
    store = make_store()
    assert len(store) == 0


def test_record_request_creates_entry() -> None:
    store = make_store()
    store.record_request("1.2.3.4", now=100.0)
    assert len(store) == 1


def test_is_per_ip_rate_exceeded_unknown_ip_false() -> None:
    store = make_store()
    assert store.is_per_ip_rate_exceeded("9.9.9.9", now=100.0) is False


def test_is_per_ip_rate_at_budget_not_exceeded() -> None:
    """Exactly per_ip_budget requests does NOT trip (strictly greater)."""
    store = make_store(per_ip_budget=5)
    for i in range(5):
        store.record_request("1.2.3.4", now=float(i))
    assert store.is_per_ip_rate_exceeded("1.2.3.4", now=4.0) is False


def test_is_per_ip_rate_one_over_budget_trips() -> None:
    store = make_store(per_ip_budget=5)
    for i in range(6):
        store.record_request("1.2.3.4", now=float(i))
    assert store.is_per_ip_rate_exceeded("1.2.3.4", now=5.0) is True


def test_sliding_window_prune_per_ip() -> None:
    """Timestamps outside the window are pruned; old burst doesn't block new window."""
    store = make_store(per_ip_budget=5, per_ip_window_s=60.0)
    for i in range(6):
        store.record_request("1.2.3.4", now=float(i))
    # At now=200, all timestamps (0..5) are > 60s old — pruned
    assert store.is_per_ip_rate_exceeded("1.2.3.4", now=200.0) is False


def test_is_global_rate_exceeded_under_budget_false() -> None:
    store = make_store(global_rate_budget=5, global_window_s=60.0)
    for i in range(5):
        store.record_request(f"ip{i}", now=float(i))
    assert store.is_global_rate_exceeded(now=4.0) is False


def test_is_global_rate_exceeded_over_budget_true() -> None:
    store = make_store(global_rate_budget=5, global_window_s=60.0)
    for i in range(6):
        store.record_request(f"ip{i}", now=float(i))
    assert store.is_global_rate_exceeded(now=5.0) is True


def test_sliding_window_prune_global() -> None:
    store = make_store(global_rate_budget=5, global_window_s=60.0)
    for i in range(6):
        store.record_request(f"ip{i}", now=float(i))
    assert store.is_global_rate_exceeded(now=200.0) is False


# --- concurrency acquire/release ---

def test_try_acquire_slot_success_returns_none() -> None:
    store = make_store(per_ip_concurrency_max=3, global_inflight_max=10)
    result = store.try_acquire_slot("1.2.3.4")
    assert result is None


def test_try_acquire_increments_inflight() -> None:
    store = make_store(per_ip_concurrency_max=3, global_inflight_max=10)
    store.try_acquire_slot("1.2.3.4")
    assert store.per_ip_inflight("1.2.3.4") == 1
    assert store.global_inflight() == 1


def test_release_slot_decrements_inflight() -> None:
    store = make_store(per_ip_concurrency_max=3, global_inflight_max=10)
    store.try_acquire_slot("1.2.3.4")
    store.release_slot("1.2.3.4")
    assert store.per_ip_inflight("1.2.3.4") == 0
    assert store.global_inflight() == 0


def test_acquire_release_pairing_multiple() -> None:
    store = make_store(per_ip_concurrency_max=5, global_inflight_max=10)
    store.try_acquire_slot("1.2.3.4")
    store.try_acquire_slot("1.2.3.4")
    assert store.per_ip_inflight("1.2.3.4") == 2
    store.release_slot("1.2.3.4")
    assert store.per_ip_inflight("1.2.3.4") == 1
    store.release_slot("1.2.3.4")
    assert store.per_ip_inflight("1.2.3.4") == 0


def test_per_ip_concurrency_cap_returns_reason() -> None:
    store = make_store(per_ip_concurrency_max=2, global_inflight_max=10)
    store.try_acquire_slot("1.2.3.4")
    store.try_acquire_slot("1.2.3.4")
    result = store.try_acquire_slot("1.2.3.4")
    assert result == REASON_PER_IP_CONCURRENCY


def test_global_inflight_cap_returns_reason() -> None:
    store = make_store(per_ip_concurrency_max=10, global_inflight_max=2)
    store.try_acquire_slot("a")
    store.try_acquire_slot("b")
    result = store.try_acquire_slot("c")
    assert result == REASON_GLOBAL_INFLIGHT


def test_concurrency_ordering_per_ip_before_global() -> None:
    """REASON_PER_IP_CONCURRENCY is returned before REASON_GLOBAL_INFLIGHT."""
    # Both caps exhausted — per-IP fires first
    store = make_store(per_ip_concurrency_max=1, global_inflight_max=1)
    store.try_acquire_slot("1.2.3.4")
    result = store.try_acquire_slot("1.2.3.4")
    assert result == REASON_PER_IP_CONCURRENCY


def test_release_slot_clamps_per_ip_at_zero() -> None:
    """release_slot on an IP with inflight=0 does not go negative."""
    store = make_store()
    store.record_request("1.2.3.4", now=1.0)
    store.release_slot("1.2.3.4")  # should not raise
    assert store.per_ip_inflight("1.2.3.4") == 0


def test_release_slot_still_works_for_unknown_ip() -> None:
    """release_slot on an IP with no record is a no-op."""
    store = make_store()
    store.release_slot("9.9.9.9")  # should not raise
    assert store.global_inflight() == 0


# --- LRU eviction ---

def test_lru_eviction_on_overflow() -> None:
    max_entries = 3
    store = make_store(per_ip_state_max_entries=max_entries)
    store.record_request("a", now=1.0)
    store.record_request("b", now=2.0)
    store.record_request("c", now=3.0)
    assert len(store) == max_entries
    store.record_request("d", now=4.0)
    assert len(store) == max_entries
    # "a" (LRU) evicted
    assert store.is_per_ip_rate_exceeded("a", now=4.0) is False
    assert store.per_ip_inflight("a") == 0


def test_eviction_mid_flight_race() -> None:
    """Acquire a slot, force eviction of that IP via N other IPs,
    then call release_slot — should not raise and global decrements."""
    store = make_store(per_ip_concurrency_max=5, global_inflight_max=10, per_ip_state_max_entries=2)
    store.try_acquire_slot("victim")
    assert store.global_inflight() == 1

    # Evict "victim" by filling table with other IPs
    store.record_request("evict1", now=1.0)
    store.record_request("evict2", now=2.0)
    # "victim" is now gone from the table
    assert store.per_ip_inflight("victim") == 0  # record gone

    # release_slot still works; global decrements even though per-IP record is gone
    store.release_slot("victim")
    assert store.global_inflight() == 0
