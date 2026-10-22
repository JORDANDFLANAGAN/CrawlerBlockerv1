from collections import OrderedDict, deque
from dataclasses import dataclass, field


@dataclass
class _IPRecord:
    request_timestamps: deque[float] = field(default_factory=deque)
    has_visited_maze: bool = False
    last_seen: float = 0.0


class DetectionStateStore:
    """In-memory per-IP state. Single-process. LRU-bounded.

    All methods are synchronous and non-blocking; safe to call from an
    async event loop without a lock (single uvicorn worker enforced by v1)."""

    def __init__(self, max_entries: int, window_seconds: float, rate_budget: int) -> None:
        self._max_entries = max_entries
        self._window_seconds = window_seconds
        self._rate_budget = rate_budget
        self._entries: OrderedDict[str, _IPRecord] = OrderedDict()

    def record_request(self, ip: str, now: float) -> None:
        """Append `now` to the IP's sliding-window deque; prune timestamps
        older than `now - window_seconds`; mark the IP as most-recently-used
        in the LRU; if the table size exceeds `max_entries`, evict the
        least-recently-used entry (NOT this one). Creates the IPRecord if
        the IP is new."""
        if ip in self._entries:
            record = self._entries[ip]
            self._entries.move_to_end(ip)
        else:
            record = _IPRecord()
            self._entries[ip] = record

        cutoff = now - self._window_seconds
        while record.request_timestamps and record.request_timestamps[0] <= cutoff:
            record.request_timestamps.popleft()
        record.request_timestamps.append(now)
        record.last_seen = now

        if len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def is_rate_exceeded(self, ip: str, now: float) -> bool:
        """True iff the IP's current sliding-window count (post-prune) is
        strictly greater than `rate_budget`. Returns False for unknown IPs."""
        if ip not in self._entries:
            return False
        record = self._entries[ip]
        cutoff = now - self._window_seconds
        count = sum(1 for ts in record.request_timestamps if ts > cutoff)
        return count > self._rate_budget

    def has_prior_maze_visit(self, ip: str) -> bool:
        """True iff `mark_maze_visit(ip)` was called earlier. False for
        unknown IPs."""
        if ip not in self._entries:
            return False
        return self._entries[ip].has_visited_maze

    def mark_maze_visit(self, ip: str) -> None:
        """Set `has_visited_maze = True` for `ip`. No-op if the IP record
        does not exist."""
        if ip in self._entries:
            self._entries[ip].has_visited_maze = True

    def __len__(self) -> int:
        """Current entry count. For observability and testing."""
        return len(self._entries)
