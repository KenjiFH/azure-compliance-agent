"""Idempotency tests. Run against the in-memory fallback (no REDIS_URL), so no Azure needed."""
import threading

import pytest

import dedup


@pytest.fixture(autouse=True)
def local_mode(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(dedup, "_client", None)
    dedup._local.clear()
    yield
    dedup._local.clear()


def test_first_claim_wins_duplicates_ignored():
    assert dedup.claim("F1") is True
    assert dedup.claim("F1") is False
    assert dedup.claim("F2") is True


def test_concurrent_claims_only_one_winner():
    results = []
    barrier = threading.Barrier(20)

    def worker():
        barrier.wait()
        results.append(dedup.claim("RACE"))

    threads = [threading.Thread(target=worker) for _ in range(20)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 1


def test_release_allows_retry():
    assert dedup.claim("F1")
    dedup.release("F1")
    assert dedup.claim("F1") is True


def test_done_stays_ignored():
    assert dedup.claim("F1")
    dedup.mark_done("F1")
    assert dedup.claim("F1") is False


def test_expired_claim_can_be_reclaimed(monkeypatch):
    assert dedup.claim("F1")
    key = dedup.PREFIX + "F1"
    dedup._local[key] = ("processing", 0)  # already expired
    assert dedup.claim("F1") is True


def test_redis_outage_falls_back_to_local(monkeypatch):
    import redis

    class Broken:
        def set(self, *a, **k): raise redis.ConnectionError("down")
        def delete(self, *a, **k): raise redis.ConnectionError("down")

    monkeypatch.setattr(dedup, "_client", Broken())
    assert dedup.claim("F1") is True      # fails open, still dedups locally
    assert dedup.claim("F1") is False
    dedup.mark_done("F1")
    dedup.release("F1")
    assert dedup.claim("F1") is True
