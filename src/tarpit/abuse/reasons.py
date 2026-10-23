from typing import Final

# Inline-defined pending T2 landing; swap to:
# from tarpit.abuse.reasons import REASON_PER_IP_RATE, REASON_PER_IP_CONCURRENCY, REASON_GLOBAL_INFLIGHT, REASON_GLOBAL_RATE
REASON_PER_IP_RATE: Final[str] = "per_ip_rate"
REASON_PER_IP_CONCURRENCY: Final[str] = "per_ip_concurrency"
REASON_GLOBAL_INFLIGHT: Final[str] = "global_inflight"
REASON_GLOBAL_RATE: Final[str] = "global_rate"
