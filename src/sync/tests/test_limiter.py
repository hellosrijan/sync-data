import time
import pytest
from django.conf import settings
import redis

from sync.limiter import RateLimiter

pytestmark = [pytest.mark.django_db]


@pytest.fixture
def test_redis():
    r = redis.Redis.from_url(settings.DRAMATIQ_BROKER["OPTIONS"]["url"])
    yield r


@pytest.fixture
def limiter(test_redis):
    lim = RateLimiter(redis_client=test_redis, default_rate=10, key_prefix="test:sync:limiter")
    lim.reset()
    yield lim
    lim.reset()


def test_ac_5_2_1_limiter_rate_and_token_acquisition(limiter):
    assert limiter.get_current_rate() == 10
    # Acquire up to capacity
    for _ in range(10):
        assert limiter.acquire(timeout=0.2) is True


def test_ac_5_2_1_limiter_blocks_when_capacity_exhausted(limiter):
    # Consume all tokens
    for _ in range(10):
        limiter.acquire(timeout=0.1)

    # Next attempt should timeout if no time elapsed
    assert limiter.acquire(timeout=0.05) is False


def test_ac_5_2_1_simulated_clock_rate_enforcement(test_redis):
    simulated_time = 1000.0

    def fake_clock():
        return simulated_time

    # Rate: 5 req/s
    lim = RateLimiter(redis_client=test_redis, default_rate=5, key_prefix="test:rate:5", clock=fake_clock)
    lim.reset()

    # 1. Acquire 5 tokens immediately
    for _ in range(5):
        assert lim.acquire(timeout=0.01) is True

    # 6th token fails immediately without clock advancing
    assert lim.acquire(timeout=0.01) is False

    # Advance clock by 1.0 second -> exactly 5 tokens refilled
    simulated_time += 1.0
    for _ in range(5):
        assert lim.acquire(timeout=0.01) is True
    assert lim.acquire(timeout=0.01) is False

    lim.reset()


def test_ac_5_2_2_throttle_halves_rate_and_pauses(limiter):
    curr = limiter.get_current_rate()
    assert curr == 10

    new_rate = limiter.throttle(retry_after=2)
    assert new_rate == 5
    assert limiter.get_current_rate() == 5

    # While paused, acquire returns False
    assert limiter.acquire(timeout=0.05) is False


def test_ac_5_2_2_recovery_increments_by_step(limiter):
    limiter.throttle(retry_after=1)
    assert limiter.get_current_rate() == 5

    r1 = limiter.recover(step=2)
    assert r1 == 7
    assert limiter.get_current_rate() == 7

    r2 = limiter.recover(step=5)
    assert r2 == 10  # capped at default_rate
    assert limiter.get_current_rate() == 10
