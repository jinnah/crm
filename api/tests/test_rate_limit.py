from app.services.rate_limit import SlidingWindowRateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_limiter(clock: FakeClock) -> SlidingWindowRateLimiter:
    return SlidingWindowRateLimiter(
        max_events=3, window_seconds=60, lockout_seconds=120, clock=clock
    )


def test_allows_under_limit() -> None:
    limiter = make_limiter(FakeClock())
    for _ in range(2):
        assert limiter.allowed("key")
        limiter.record("key")
    assert limiter.allowed("key")


def test_blocks_at_limit_and_locks_out() -> None:
    clock = FakeClock()
    limiter = make_limiter(clock)
    for _ in range(3):
        limiter.record("key")
    assert not limiter.allowed("key")
    clock.advance(119)
    assert not limiter.allowed("key")
    clock.advance(2)  # lockout expired
    assert limiter.allowed("key")


def test_window_slides() -> None:
    clock = FakeClock()
    limiter = make_limiter(clock)
    limiter.record("key")
    limiter.record("key")
    clock.advance(61)  # both events fall out of the window
    assert limiter.allowed("key")
    limiter.record("key")
    assert limiter.allowed("key")


def test_keys_are_independent() -> None:
    limiter = make_limiter(FakeClock())
    for _ in range(3):
        limiter.record("a")
    assert not limiter.allowed("a")
    assert limiter.allowed("b")


def test_reset_clears_key() -> None:
    limiter = make_limiter(FakeClock())
    for _ in range(3):
        limiter.record("key")
    assert not limiter.allowed("key")
    limiter.reset("key")
    assert limiter.allowed("key")


def test_storage_stays_bounded_under_unique_key_churn() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(
        max_events=3, window_seconds=60, lockout_seconds=120, max_keys=100, clock=clock
    )
    for i in range(10_000):
        key = f"key-{i}"
        limiter.allowed(key)
        limiter.record(key)
        clock.advance(0.001)
    assert limiter.tracked_keys() <= 100


def test_stale_entries_are_swept() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(
        max_events=3, window_seconds=60, lockout_seconds=120, max_keys=1000, clock=clock
    )
    for i in range(500):
        limiter.record(f"key-{i}")
    clock.advance(61)  # every event now stale, no lockouts active
    # Amortized sweeps run every 256 operations.
    for _ in range(300):
        limiter.allowed("probe")
    assert limiter.tracked_keys() <= 1


def test_active_lockout_survives_key_churn() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(
        max_events=3, window_seconds=60, lockout_seconds=600, max_keys=50, clock=clock
    )
    for _ in range(3):
        limiter.record("victim")
    assert not limiter.allowed("victim")
    for i in range(5_000):
        limiter.record(f"churn-{i}")
        clock.advance(0.001)
    # The locked key must not have been evicted to make room for churn.
    assert not limiter.allowed("victim")
    assert limiter.tracked_keys() <= 50


def test_fails_closed_when_full_of_active_lockouts() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(
        max_events=1, window_seconds=60, lockout_seconds=600, max_keys=5, clock=clock
    )
    for i in range(5):
        limiter.record(f"locked-{i}")  # max_events=1: each record locks the key
        assert not limiter.allowed(f"locked-{i}")
    # Storage is exhausted by active lockouts; new keys are denied, not stored.
    assert not limiter.allowed("newcomer")
    limiter.record("newcomer")
    assert limiter.tracked_keys() == 5
    # After the lockouts expire, capacity frees and new keys are allowed again.
    clock.advance(601)
    assert limiter.allowed("newcomer")
