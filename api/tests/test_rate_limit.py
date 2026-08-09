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
