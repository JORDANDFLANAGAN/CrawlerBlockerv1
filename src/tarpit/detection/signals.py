import re
from typing import Final

from fastapi import Request

from tarpit.detection.state import DetectionStateStore

SIGNAL_UA_MISSING: Final[str] = "ua_missing"
SIGNAL_UA_KNOWN_BOT_PATTERN: Final[str] = "ua_known_bot_pattern"
SIGNAL_RATE_EXCEEDED: Final[str] = "rate_exceeded"
SIGNAL_PRIOR_MAZE_VISIT: Final[str] = "prior_maze_visit"

_UA_BOT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"bot|crawler|spider", re.IGNORECASE
)


def evaluate_request(
    request: Request,
    store: DetectionStateStore,
    now: float,
) -> list[str]:
    """Return the signal ids that fired, in a deterministic order:
    [ua_missing | ua_known_bot_pattern, rate_exceeded, prior_maze_visit].

    `ua_missing` and `ua_known_bot_pattern` are mutually exclusive: an
    absent/empty UA cannot also match the pattern. If both would fire,
    only `ua_missing` is reported.

    Pure read; does not mutate `store`."""
    triggers: list[str] = []

    ua = request.headers.get("user-agent")
    if not ua:
        triggers.append(SIGNAL_UA_MISSING)
    elif _UA_BOT_PATTERN.search(ua):
        triggers.append(SIGNAL_UA_KNOWN_BOT_PATTERN)

    ip = request.client.host if request.client else "unknown"

    if store.is_rate_exceeded(ip, now):
        triggers.append(SIGNAL_RATE_EXCEEDED)

    if store.has_prior_maze_visit(ip):
        triggers.append(SIGNAL_PRIOR_MAZE_VISIT)

    return triggers
