"""Durable Redis job queue.

Uses a reliable-queue pattern: BRPOPLPUSH atomically moves a job from the
pending list to a per-worker processing list, so a job is never lost if the
worker dies between pop and completion. Orphaned jobs are swept back by
``requeue_stalled`` and by the database-side heartbeat check.
"""

from __future__ import annotations

import asyncio
import json
import socket
import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# Blocking reserve: how long BRPOPLPUSH parks server-side before returning nil.
RESERVE_BLOCK_SECONDS = 5

QUEUE_KEY = "clauseguard:analyses:pending"
PROCESSING_PREFIX = "clauseguard:analyses:processing"
DEDUPE_PREFIX = "clauseguard:analyses:enqueued"
HEARTBEAT_PREFIX = "clauseguard:workers"
DEDUPE_TTL_SECONDS = 3600


def make_worker_id() -> str:
    return f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


def build_worker_redis(redis_url: str) -> Any:
    """Redis client for the worker's blocking consumer loop.

    ``socket_timeout=None`` is the important part. redis-py 8.x defaults it to
    5 seconds, which races the 5-second BRPOPLPUSH block and raises
    ``redis.exceptions.TimeoutError`` on essentially every idle poll. A blocking
    read must not have a deadline shorter than the block it is waiting on.

    ``socket_connect_timeout`` stays bounded so a genuinely unreachable Redis
    fails quickly and visibly instead of hanging the worker forever.
    """
    import redis.asyncio as aioredis

    return aioredis.from_url(
        redis_url,
        decode_responses=True,
        socket_timeout=None,  # no read deadline: blocking pops park here
        socket_connect_timeout=5,  # but connecting must not hang
        health_check_interval=30,
        retry_on_timeout=True,
    )


def build_api_redis(redis_url: str) -> Any:
    """Redis client for the API.

    The API issues only short, non-blocking commands (rate limiting, enqueue,
    health), so a bounded read timeout is correct here - a slow Redis should
    surface quickly rather than stall a request.
    """
    import redis.asyncio as aioredis

    return aioredis.from_url(
        redis_url,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5,
        health_check_interval=30,
        retry_on_timeout=True,
    )


@dataclass
class Job:
    analysis_id: str
    org_id: str
    document_id: str
    policy_id: str
    enqueued_at: float
    raw: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "analysis_id": self.analysis_id,
                "org_id": self.org_id,
                "document_id": self.document_id,
                "policy_id": self.policy_id,
                "enqueued_at": self.enqueued_at,
            }
        )

    @staticmethod
    def from_json(payload: str) -> Job:
        data = json.loads(payload)
        return Job(
            analysis_id=data["analysis_id"],
            org_id=data["org_id"],
            document_id=data["document_id"],
            policy_id=data["policy_id"],
            enqueued_at=data.get("enqueued_at", 0.0),
            raw=payload,
        )


class AnalysisQueue:
    def __init__(self, redis_client) -> None:
        self.redis = redis_client

    async def enqueue(
        self, *, analysis_id: str, org_id: str, document_id: str, policy_id: str
    ) -> bool:
        """Enqueue a job. Returns False when the job is already queued.

        The dedupe key means a double-clicked "Analyze" button cannot produce
        two runs even if two API requests both create records.
        """
        dedupe_key = f"{DEDUPE_PREFIX}:{analysis_id}"
        claimed = await self.redis.set(dedupe_key, "1", nx=True, ex=DEDUPE_TTL_SECONDS)
        if not claimed:
            logger.info("duplicate job suppressed", extra={"analysis_id": analysis_id})
            return False

        job = Job(
            analysis_id=analysis_id,
            org_id=org_id,
            document_id=document_id,
            policy_id=policy_id,
            enqueued_at=time.time(),
        )
        await self.redis.lpush(QUEUE_KEY, job.to_json())
        return True

    async def reserve(self, worker_id: str, timeout: int = RESERVE_BLOCK_SECONDS) -> Job | None:
        """Reserve one job, or return None if the queue stayed empty.

        An empty queue is normal idle behaviour, not an error. BRPOPLPUSH parks
        server-side for `timeout` seconds and then returns nil; on some client
        configurations the client-side read deadline fires at the same moment
        and surfaces as a TimeoutError instead. Both mean the same thing, so
        both return None here and the caller simply loops.

        Note that redis-py 8.x defaults `socket_timeout` to 5 seconds. Without
        the explicit `socket_timeout=None` set in `build_worker_redis()`, that
        default races this block and raises on nearly every idle poll.
        """
        import redis.exceptions as redis_exceptions

        try:
            payload = await self.redis.brpoplpush(
                QUEUE_KEY, f"{PROCESSING_PREFIX}:{worker_id}", timeout=timeout
            )
        # asyncio.TimeoutError only became an alias of the builtin TimeoutError
        # in Python 3.11; naming all three keeps this correct on 3.10 as well.
        except (  # noqa: UP041
            redis_exceptions.TimeoutError,
            asyncio.TimeoutError,
            TimeoutError,
        ):
            # Idle. Deliberately not logged: this fires every few seconds on a
            # quiet queue and would drown the log.
            return None

        if not payload:
            return None
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        try:
            return Job.from_json(payload)
        except (json.JSONDecodeError, KeyError):
            logger.error("malformed job discarded")
            await self.redis.lrem(f"{PROCESSING_PREFIX}:{worker_id}", 1, payload)
            return None

    async def acknowledge(self, worker_id: str, job: Job) -> None:
        await self.redis.lrem(f"{PROCESSING_PREFIX}:{worker_id}", 1, job.raw or job.to_json())
        await self.redis.delete(f"{DEDUPE_PREFIX}:{job.analysis_id}")

    async def retry(self, worker_id: str, job: Job) -> None:
        """Return a job to the queue after a transient failure."""
        await self.redis.lrem(f"{PROCESSING_PREFIX}:{worker_id}", 1, job.raw or job.to_json())
        await self.redis.lpush(QUEUE_KEY, job.to_json())

    async def requeue_stalled(self, worker_id: str) -> int:
        """Recover jobs left in a dead worker's processing list."""
        key = f"{PROCESSING_PREFIX}:{worker_id}"
        recovered = 0
        while True:
            payload = await self.redis.rpoplpush(key, QUEUE_KEY)
            if not payload:
                break
            recovered += 1
        if recovered:
            logger.warning(
                "requeued orphaned jobs", extra={"count": recovered, "worker": worker_id}
            )
        return recovered

    async def heartbeat(self, worker_id: str, ttl: int = 60) -> None:
        await self.redis.set(f"{HEARTBEAT_PREFIX}:{worker_id}", time.time(), ex=ttl)

    async def depth(self) -> int:
        try:
            return int(await self.redis.llen(QUEUE_KEY) or 0)
        except Exception:  # noqa: BLE001
            return -1

    async def live_workers(self) -> int:
        try:
            keys = await self.redis.keys(f"{HEARTBEAT_PREFIX}:*")
            return len(keys or [])
        except Exception:  # noqa: BLE001
            return -1

    async def health(self) -> dict[str, Any]:
        try:
            await self.redis.ping()
            return {
                "connected": True,
                "queue_depth": await self.depth(),
                "live_workers": await self.live_workers(),
            }
        except Exception as exc:  # noqa: BLE001
            return {"connected": False, "error": type(exc).__name__, "detail": str(exc)[:200]}
