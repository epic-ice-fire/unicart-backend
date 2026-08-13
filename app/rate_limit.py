import asyncio
import time
from collections import defaultdict, deque

from fastapi import HTTPException


class SlidingWindowRateLimiter:
    """Small in-process limiter for a single API instance.

    This is intentionally dependency-free for the current UniCart deployment.
    If UniCart later runs multiple API instances, move this state to Redis so
    limits are shared across all workers/instances.
    """

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def enforce(
        self,
        *,
        key: str,
        limit: int,
        window_seconds: int,
        detail: str = "Too many requests. Please try again later.",
    ) -> None:
        now = time.monotonic()
        cutoff = now - window_seconds

        async with self._lock:
            bucket = self._events[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            if len(bucket) >= limit:
                retry_after = max(1, int(window_seconds - (now - bucket[0])))
                raise HTTPException(
                    status_code=429,
                    detail=detail,
                    headers={"Retry-After": str(retry_after)},
                )

            bucket.append(now)

    async def reset(self, key: str) -> None:
        async with self._lock:
            self._events.pop(key, None)


rate_limiter = SlidingWindowRateLimiter()
