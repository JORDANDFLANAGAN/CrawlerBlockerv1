import random
import re
from typing import Final

ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._~-]{1,64}$")
NEW_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{4}$")
BAIT_ID: Final[str] = "root"


def generate_new_ids(rng: random.Random, count: int) -> list[str]:
    """Return `count` 4-char lowercase hex strings drawn from `rng`.

    Deterministic for a given rng state. May include duplicates (callers
    that need uniqueness within the output must dedupe)."""
    return [format(rng.randint(0, 0xFFFF), "04x") for _ in range(count)]


def is_valid_id(id_: str) -> bool:
    """True iff id_ matches ID_PATTERN."""
    return bool(ID_PATTERN.match(id_))
