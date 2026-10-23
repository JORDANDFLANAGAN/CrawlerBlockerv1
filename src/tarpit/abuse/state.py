from collections import OrderedDict, deque
from dataclasses import dataclass, field

from tarpit.abuse.reasons import (
    REASON_GLOBAL_INFLIGHT,
    REASON_PER_IP_CONCURRENCY,
)


@dataclass
class _IPRecord:
    request_timestamps: deque[float] = field(default_factory=deque)
    inflight: int = 0
    last_seen: float = 0.0


class AbuseStateStore:
    """In-memory per-IP + global abuse protection state. Single-process. LRU-bounded.

    All methods are synchronous and non-blocking; safe to call from an
    async event loop without no lock (workers=1, no await inside any method)."""

    def __init__(
        self,
        *,
        per_ip_window_s: float,
        per_ip_budget: int,
        per_ip_concurrency_max: int,
        per_ip_state_max_entries: int,
        global_window_s: float,
        global_rate_budget: int,
        global_inflight_max: int,
    ) -> None:
        self._per_ip_window_s = per_ip_window_s
        self._per_ip_budget = per_ip_budget
        self._per_ip_concurrency_max = per_ip_concurrency_max
        self._per_ip_state_max_entries = per_ip_state_max_entries
        self._global_window_s = global_window_s
        self._global_rate_budget = global_rate_budget
        self._global_inflight_max = global_inflight_max

        self._entries: OrderedDict[str, _IPRecord] = OrderedDict()
        self._global_timestamps: deque[float] = deque()
        self._global_inflight: int = 0

    def _get_or_create(self, ip: str) -> _IPRecord:
        if ip in self._entries:
            record = self._entries[ip]
            self._entries.move_to_end(ip)
        else:
            record = _IPRecord()
            self._entries[ip] = record
        return record

    def _evict_if_needed(self) -> None:
        if len(self._entries) > self._per_ip_state_max_entries:
            self._entries.popitem(last=False)

    def record_request(self, ip: str, now: float) -> None:
        """Append now to this IP's deque. Prune < now - per_ip_window_s.
        Touch LRU. Evict LRU if size > per_ip_state_max_entries.
        Also append to the global sliding-window deque (same prune rule
        with global_window_s). Counts EVERY call, including requests
        that will later be denied (REQ-RL-05)."""
        record = self._get_or_create(ip)

        cutoff = now - self._per_ip_window_s
        while record.request_timestamps and record.request_timestamps[0] <= cutoff:
            record.request_timestamps.popleft()
        record.request_timestamps.append(now)
        record.last_seen = now

        self._evict_if_needed()

        global_cutoff = now - self._global_window_s
        while self._global_timestamps and self._global_timestamps[0] <= global_cutoff:
            self._global_timestamps.popleft()
        self._global_timestamps.append(now)

    def is_per_ip_rate_exceeded(self, ip: str, now: float) -> bool:
        """True iff post-prune count > per_ip_budget. Unknown IP -> False."""
        if ip not in self._entries:
            return False
        record = self._entries[ip]
        cutoff = now - self._per_ip_window_s
        count = sum(1 for ts in record.request_timestamps if ts > cutoff)
        return count > self._per_ip_budget

    def is_global_rate_exceeded(self, now: float) -> bool:
        """True iff post-prune global count > global_rate_budget."""
        cutoff = now - self._global_window_s
        count = sum(1 for ts in self._global_timestamps if ts > cutoff)
        return count > self._global_rate_budget

    def try_acquire_slot(self, ip: str) -> str | None:
        """Attempt to reserve one per-IP + one global in-flight slot.
        Returns None on success. On failure returns the reason constant
        that blocked (REASON_PER_IP_CONCURRENCY checked first,
        REASON_GLOBAL_INFLIGHT second). On success, both counters are
        incremented atomically from the caller's point of view."""
        record = self._entries.get(ip)
        per_ip_count = record.inflight if record is not None else 0

        if per_ip_count >= self._per_ip_concurrency_max:
            return REASON_PER_IP_CONCURRENCY

        if self._global_inflight >= self._global_inflight_max:
            return REASON_GLOBAL_INFLIGHT

        if record is None:
            record = self._get_or_create(ip)
            self._evict_if_needed()

        record.inflight += 1
        self._global_inflight += 1
        return None

    def release_slot(self, ip: str) -> None:
        """Decrement per-IP + global in-flight. MUST be called exactly
        once per successful try_acquire_slot. Safe to call for an IP
        whose per-IP counter is 0 (clamps at 0)."""
        record = self._entries.get(ip)
        if record is not None and record.inflight > 0:
            record.inflight -= 1
        # Decrement global unconditionally — handles LRU-eviction race
        if self._global_inflight > 0:
            self._global_inflight -= 1

    def per_ip_inflight(self, ip: str) -> int:
        record = self._entries.get(ip)
        return record.inflight if record is not None else 0

    def global_inflight(self) -> int:
        return self._global_inflight

    def __len__(self) -> int:
        return len(self._entries)
