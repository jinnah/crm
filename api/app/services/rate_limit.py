import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Protocol


class RateLimiter(Protocol):
    """Replaceable throttling dependency; the default is in-memory and
    suitable for the single API instance this project deploys."""

    def allowed(self, key: str) -> bool: ...

    def record(self, key: str) -> None: ...

    def reset(self, key: str) -> None: ...


class SlidingWindowRateLimiter:
    """Counts events per key in a sliding window; exceeding the limit locks
    the key out for `lockout_seconds`."""

    def __init__(
        self,
        max_events: int,
        window_seconds: float,
        lockout_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_events = max_events
        self._window = window_seconds
        self._lockout = lockout_seconds
        self._clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> None:
        events = self._events[key]
        while events and now - events[0] > self._window:
            events.popleft()

    def allowed(self, key: str) -> bool:
        with self._lock:
            now = self._clock()
            locked_until = self._locked_until.get(key)
            if locked_until is not None:
                if now < locked_until:
                    return False
                del self._locked_until[key]
                self._events[key].clear()
            self._prune(key, now)
            return len(self._events[key]) < self._max_events

    def record(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            self._prune(key, now)
            events = self._events[key]
            events.append(now)
            if len(events) >= self._max_events:
                self._locked_until[key] = now + self._lockout

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)
            self._locked_until.pop(key, None)


def default_login_limiter() -> SlidingWindowRateLimiter:
    # 5 failures per 15 minutes per key (source IP or account), 15-minute lockout.
    return SlidingWindowRateLimiter(max_events=5, window_seconds=900, lockout_seconds=900)


def default_recovery_limiter() -> SlidingWindowRateLimiter:
    # 3 recovery requests per 15 minutes per key, 15-minute lockout.
    return SlidingWindowRateLimiter(max_events=3, window_seconds=900, lockout_seconds=900)
