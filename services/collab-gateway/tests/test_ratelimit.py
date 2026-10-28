from ratelimit import RateLimiter


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_allows_up_to_limit_then_blocks():
    clock = Clock()
    limiter = RateLimiter(3, clock=clock)
    assert [limiter.allow("u") for _ in range(4)] == [True, True, True, False]


def test_window_slides():
    clock = Clock()
    limiter = RateLimiter(2, clock=clock)
    assert limiter.allow("u") and limiter.allow("u") and not limiter.allow("u")
    clock.now += 61
    assert limiter.allow("u")


def test_keys_are_independent():
    limiter = RateLimiter(1, clock=Clock())
    assert limiter.allow("a") and limiter.allow("b") and not limiter.allow("a")


def test_blocked_attempts_do_not_extend_the_window():
    clock = Clock()
    limiter = RateLimiter(1, clock=clock)
    assert limiter.allow("u")
    clock.now += 30
    assert not limiter.allow("u")
    clock.now += 31
    assert limiter.allow("u")


def test_tracked_keys_are_bounded():
    clock = Clock()
    limiter = RateLimiter(5, clock=clock, max_keys=100)
    for i in range(500):
        limiter.allow(f"user-{i}")
        clock.now += 0.01
    assert len(limiter._hits) <= 100


def test_expired_keys_are_dropped():
    clock = Clock()
    limiter = RateLimiter(5, clock=clock, max_keys=10)
    for i in range(10):
        limiter.allow(f"user-{i}")
    clock.now += 120
    limiter.allow("fresh")
    assert set(limiter._hits) == {"fresh"}
