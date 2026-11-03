"""Fixed-window rate limits for endpoints that send email, spend model credits or create PayPal orders.

Counted in memory per app instance, keyed by scope and client (IP address, or the caller's token). That is enough
for one web instance; a deployment with several instances still gets a per-instance ceiling. Each window is an
hour, so a limit of 20 means 20 requests per hour per client. 0 turns a limit off.
"""

import threading
import time
from dataclasses import dataclass, field

from app.services import ServiceError

WINDOW_SECONDS = 3600.0


class RateLimited(ServiceError):
    def __init__(self, retry_after: int):
        super().__init__("too many requests; try again later", 429)
        self.retry_after = max(1, retry_after)


@dataclass
class RateLimiter:
    clock: object = time.monotonic
    _hits: dict[tuple[str, str], tuple[float, int]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def check(self, scope: str, key: str, limit: int) -> None:
        """Count one request; raise RateLimited (HTTP 429) once `limit` is exceeded within the window."""
        if limit <= 0:
            return
        now = self.clock()  # type: ignore[operator]
        with self._lock:
            if len(self._hits) > 10_000:  # forget windows that have closed
                self._hits = {k: v for k, v in self._hits.items() if now - v[0] < WINDOW_SECONDS}
            started, count = self._hits.get((scope, key), (now, 0))
            if now - started >= WINDOW_SECONDS:
                started, count = now, 0
            count += 1
            self._hits[(scope, key)] = (started, count)
            if count > limit:
                raise RateLimited(int(WINDOW_SECONDS - (now - started)) + 1)
