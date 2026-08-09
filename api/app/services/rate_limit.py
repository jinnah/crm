import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Protocol


class RateLimiter(Protocol):
    """Replaceable throttling dependency; the default is in-memory and
    suitable for the single API instance this project deploys."""

    def allowed(self, key: str) -> bool: ...

    def record(self, key: str) -> None: ...

    def reset(self, key: str) -> None: ...


class _Entry:
    __slots__ = ("events", "locked_until", "last_seen")

    def __init__(self, now: float) -> None:
        self.events: deque[float] = deque()
        self.locked_until: float | None = None
        self.last_seen = now


class SlidingWindowRateLimiter:
    """Counts events per key in a sliding window; exceeding the limit locks
    the key out for `lockout_seconds`.

    Memory is bounded: at most `max_keys` keys are tracked. Stale entries are
    swept periodically and on capacity pressure. When capacity is reached,
    the stalest keys without an active lockout are evicted; active lockouts
    are never evicted, and if every tracked key is actively locked out, new
    keys are denied (fail closed) rather than growing storage.
    """

    _SWEEP_INTERVAL = 256  # operations between amortized full sweeps

    def __init__(
        self,
        max_events: int,
        window_seconds: float,
        lockout_seconds: float,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_events = max_events
        self._window = window_seconds
        self._lockout = lockout_seconds
        self._max_keys = max_keys
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        self._op_count = 0

    # All private helpers below assume self._lock is held.

    def _is_locked(self, entry: _Entry, now: float) -> bool:
        return entry.locked_until is not None and now < entry.locked_until

    def _is_stale(self, entry: _Entry, now: float) -> bool:
        if self._is_locked(entry, now):
            return False
        return not entry.events or now - entry.events[-1] > self._window

    def _prune_events(self, entry: _Entry, now: float) -> None:
        while entry.events and now - entry.events[0] > self._window:
            entry.events.popleft()

    def _sweep(self, now: float) -> None:
        stale = [key for key, entry in self._entries.items() if self._is_stale(entry, now)]
        for key in stale:
            del self._entries[key]

    def _maybe_sweep(self, now: float) -> None:
        self._op_count += 1
        if self._op_count % self._SWEEP_INTERVAL == 0:
            self._sweep(now)

    def _get_or_create(self, key: str, now: float) -> _Entry | None:
        """Return the entry for key, creating it if capacity allows; None means
        the limiter is full of active lockouts and the key is denied."""
        entry = self._entries.get(key)
        if entry is not None:
            entry.last_seen = now
            return entry
        if len(self._entries) >= self._max_keys:
            self._sweep(now)
        if len(self._entries) >= self._max_keys:
            evictable = [
                (candidate.last_seen, candidate_key)
                for candidate_key, candidate in self._entries.items()
                if not self._is_locked(candidate, now)
            ]
            if not evictable:
                return None  # fail closed; never weaken an active lockout
            evictable.sort()
            for _, evict_key in evictable[: max(1, len(evictable) // 100)]:
                del self._entries[evict_key]
        entry = _Entry(now)
        self._entries[key] = entry
        return entry

    def allowed(self, key: str) -> bool:
        with self._lock:
            now = self._clock()
            self._maybe_sweep(now)
            entry = self._entries.get(key)
            if entry is None:
                # Unknown keys are allowed unless storage is exhausted by
                # active lockouts, in which case we fail closed.
                return not (
                    len(self._entries) >= self._max_keys
                    and all(self._is_locked(candidate, now) for candidate in self._entries.values())
                )
            if self._is_locked(entry, now):
                return False
            if entry.locked_until is not None:
                entry.locked_until = None
                entry.events.clear()
            self._prune_events(entry, now)
            return len(entry.events) < self._max_events

    def record(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            self._maybe_sweep(now)
            entry = self._get_or_create(key, now)
            if entry is None:
                return  # denied by allowed(); nothing to track
            self._prune_events(entry, now)
            entry.events.append(now)
            if len(entry.events) >= self._max_events:
                entry.locked_until = now + self._lockout

    def reset(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def tracked_keys(self) -> int:
        """Number of keys currently held; exposed for capacity tests."""
        with self._lock:
            return len(self._entries)


def default_login_limiter() -> SlidingWindowRateLimiter:
    # 5 failures per 15 minutes per key (source IP or account), 15-minute lockout.
    return SlidingWindowRateLimiter(max_events=5, window_seconds=900, lockout_seconds=900)


def default_recovery_limiter() -> SlidingWindowRateLimiter:
    # 3 recovery requests per 15 minutes per key, 15-minute lockout.
    return SlidingWindowRateLimiter(max_events=3, window_seconds=900, lockout_seconds=900)
