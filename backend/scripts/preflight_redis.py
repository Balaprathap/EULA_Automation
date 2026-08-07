"""Preflight: verify REDIS_URL supports the commands the worker depends on.

The analysis worker reserves jobs with a blocking BRPOPLPUSH. Some managed and
serverless Redis products reject blocking commands, which produces the worst
kind of failure: the worker starts, logs nothing, reports healthy, and silently
never claims a job.

Run this before deploying, and again after changing REDIS_URL:

    python -m scripts.preflight_redis

Exits 0 when the queue will work, 1 when it will not.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
import uuid

PROBE_PREFIX = "clauseguard:preflight"


async def run() -> int:
    url = os.environ.get("REDIS_URL")
    if not url:
        print("ERROR: REDIS_URL is not set.", file=sys.stderr)
        return 1

    # Never print the URL - it carries credentials.
    scheme = url.split("://", 1)[0] if "://" in url else "?"
    print(f"Checking Redis ({scheme}://…)\n")

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app.jobs.queue import build_worker_redis

    client = build_worker_redis(url)
    source = f"{PROBE_PREFIX}:src:{uuid.uuid4().hex[:8]}"
    destination = f"{PROBE_PREFIX}:dst:{uuid.uuid4().hex[:8]}"
    failures: list[str] = []

    try:
        # 1. Connectivity
        try:
            await client.ping()
            print("  ok    PING")
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL  PING -> {type(exc).__name__}: {exc}")
            return 1

        # 2. TLS advisory
        if scheme == "redis":
            print("  warn  connection is not TLS; prefer rediss:// in production")
        else:
            print("  ok    TLS scheme (rediss://)")

        # 3. Blocking pop on an EMPTY queue must return None, not raise.
        started = time.perf_counter()
        try:
            result = await client.brpoplpush(source, destination, timeout=2)
            elapsed = time.perf_counter() - started
            if result is None:
                print(f"  ok    BRPOPLPUSH on an empty queue returned None after {elapsed:.1f}s")
            else:
                print(f"  warn  unexpected payload from an empty queue: {result!r}")
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            print(f"  FAIL  BRPOPLPUSH rejected or timed out -> {name}: {exc}")
            failures.append(
                "This Redis does not support blocking BRPOPLPUSH. The worker would start, "
                "look healthy, and never claim a job. Use a standard Redis instance "
                "(or Google MemoryStore) rather than a tier that disallows blocking commands."
            )

        # 4. Blocking pop with a WAITING job must return it promptly.
        if not failures:
            try:
                await client.lpush(source, "preflight-job")
                started = time.perf_counter()
                payload = await client.brpoplpush(source, destination, timeout=5)
                elapsed = time.perf_counter() - started
                if payload == "preflight-job" and elapsed < 2:
                    print(f"  ok    BRPOPLPUSH reserved a queued job in {elapsed:.2f}s")
                else:
                    print(
                        f"  FAIL  expected the job back quickly, got {payload!r} in {elapsed:.2f}s"
                    )
                    failures.append("Blocking reserve did not return a queued job promptly.")
            except Exception as exc:  # noqa: BLE001
                print(f"  FAIL  reserve of a queued job -> {type(exc).__name__}: {exc}")
                failures.append("Blocking reserve failed with a job present.")

        # 5. Commands the rate limiter and heartbeat need.
        for label, coro in (
            ("SET/EX (rate limiting, dedupe)", client.set(f"{PROBE_PREFIX}:k", "1", ex=10)),
            ("ZADD (sliding-window rate limit)", client.zadd(f"{PROBE_PREFIX}:z", {"a": 1.0})),
            ("LLEN (queue depth)", client.llen(source)),
        ):
            try:
                await coro
                print(f"  ok    {label}")
            except Exception as exc:  # noqa: BLE001
                print(f"  FAIL  {label} -> {type(exc).__name__}")
                failures.append(f"{label} is unavailable.")

    finally:
        # Cleanup is best effort; a failure here must not mask the result above.
        with contextlib.suppress(Exception):
            await client.delete(source, destination, f"{PROBE_PREFIX}:k", f"{PROBE_PREFIX}:z")
            await client.aclose()

    print()
    if failures:
        print("RESULT: this Redis is NOT suitable for the analysis worker.\n")
        for problem in failures:
            print(f"  - {problem}")
        return 1
    print("RESULT: Redis supports everything the worker needs.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
