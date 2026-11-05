"""Idempotency guard for Slack events, backed by Azure Cache for Redis.

One Slack upload can arrive several times (retries, plus `message` and `file_shared`
for the same file). `claim()` is an atomic `SET key value NX EX ttl`, so exactly one
caller wins no matter how many replicas receive the event.

Lifecycle of a key (`slack:file:<file_id>`):
    claim()      -> "processing"  (short TTL: a crashed worker frees the file on its own)
    mark_done()  -> "done"        (longer TTL: late retries and re-deliveries stay ignored)
    release()    -> key deleted   (worker failed, so a re-upload can be processed again)

Configuration: REDIS_URL, e.g. rediss://:<access-key>@<name>.redis.cache.windows.net:6380/0
If REDIS_URL is unset (local dev) or Redis is unreachable, falls back to a per-process
in-memory store and logs a warning, so uploads are never dropped because of a cache outage.
"""
import os
import threading
import time

import redis

PROCESSING_TTL = int(os.getenv("DEDUP_PROCESSING_TTL", "900"))   # 15 min: longer than a normal run
DONE_TTL = int(os.getenv("DEDUP_DONE_TTL", "86400"))             # 24 h
PREFIX = "slack:file:"

_client = None
_client_lock = threading.Lock()

# In-memory fallback: {key: (value, expires_at)}
_local = {}
_local_lock = threading.Lock()


def _redis():
    """Lazily build the client; None means 'use the local fallback'."""
    global _client
    if _client is not None:
        return _client
    url = os.getenv("REDIS_URL")
    if not url:
        return None
    with _client_lock:
        if _client is None:
            _client = redis.Redis.from_url(
                url,
                socket_connect_timeout=2,
                socket_timeout=2,
                health_check_interval=30,
                decode_responses=True,
            )
    return _client


def _local_claim(key: str, ttl: int) -> bool:
    now = time.time()
    with _local_lock:
        for k in [k for k, (_, exp) in _local.items() if exp <= now]:
            del _local[k]
        if key in _local:
            return False
        _local[key] = ("processing", now + ttl)
        return True


def _local_set(key: str, value: str, ttl: int) -> None:
    with _local_lock:
        _local[key] = (value, time.time() + ttl)


def claim(file_id: str) -> bool:
    """True if this caller is the first to see `file_id` and should process it."""
    key = PREFIX + file_id
    r = _redis()
    if r is not None:
        try:
            return bool(r.set(key, "processing", nx=True, ex=PROCESSING_TTL))
        except redis.RedisError as e:
            print(f"⚠️ Redis unavailable ({e}); using in-memory dedup for {file_id}")
    return _local_claim(key, PROCESSING_TTL)


def mark_done(file_id: str) -> None:
    """Processing finished: keep the file ignored for DONE_TTL."""
    key = PREFIX + file_id
    r = _redis()
    if r is not None:
        try:
            r.set(key, "done", ex=DONE_TTL)
            return
        except redis.RedisError as e:
            print(f"⚠️ Redis unavailable ({e}); marking done locally for {file_id}")
    _local_set(key, "done", DONE_TTL)


def release(file_id: str) -> None:
    """Processing failed: let a retry or re-upload of this file run again."""
    key = PREFIX + file_id
    r = _redis()
    if r is not None:
        try:
            r.delete(key)
        except redis.RedisError as e:
            print(f"⚠️ Redis unavailable ({e}); key {key} will expire after {PROCESSING_TTL}s")
    with _local_lock:
        _local.pop(key, None)
