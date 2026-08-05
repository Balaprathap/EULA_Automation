"""Worker queue behaviour: idle polling, reservation, outage handling, shutdown.

Regression context: the worker logged `worker loop error / TimeoutError` roughly
every five seconds while the queue was empty. redis-py 8.x changed
DEFAULT_SOCKET_TIMEOUT from None to 5 seconds, so the client's read deadline
raced the 5-second BRPOPLPUSH block and lost. An idle queue is not an error.

Tests marked `redis_server` run against a real redis-server binary when one is
available, and skip cleanly otherwise.
"""

import asyncio
import os
import shutil
import socket
import subprocess
import time

import pytest
import redis.exceptions as redis_exceptions

from app.jobs.queue import (
    QUEUE_KEY,
    AnalysisQueue,
    Job,
    build_api_redis,
    build_worker_redis,
    make_worker_id,
)


def find_redis_server() -> str | None:
    binary = shutil.which("redis-server")
    if binary:
        return binary
    try:
        import redislite

        candidate = os.path.join(os.path.dirname(redislite.__file__), "bin", "redis-server")
        return candidate if os.path.exists(candidate) else None
    except ImportError:
        return None


REDIS_BINARY = find_redis_server()
requires_redis = pytest.mark.skipif(REDIS_BINARY is None, reason="no redis-server binary available")


@pytest.fixture(scope="module")
def redis_url():
    if REDIS_BINARY is None:
        pytest.skip("no redis-server binary available")
    port = 7411
    process = subprocess.Popen(
        [REDIS_BINARY, "--port", str(port), "--save", "", "--appendonly", "no"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    else:
        process.terminate()
        pytest.skip("redis-server did not start")
    yield f"redis://127.0.0.1:{port}/0"
    process.terminate()
    process.wait(timeout=10)


class TestClientConfiguration:
    """The root cause was client construction, so pin it directly."""

    def test_worker_client_has_no_read_deadline(self):
        client = build_worker_redis("redis://localhost:6379/0")
        connection = client.connection_pool.make_connection()
        assert connection.socket_timeout is None, (
            "a blocking BRPOPLPUSH must not have a read deadline shorter than the block"
        )

    def test_worker_client_still_bounds_connect_time(self):
        connection = build_worker_redis(
            "redis://localhost:6379/0"
        ).connection_pool.make_connection()
        assert connection.socket_connect_timeout == 5

    def test_api_client_keeps_a_bounded_read_timeout(self):
        """The API issues only short commands; a slow Redis should surface fast."""
        connection = build_api_redis("redis://localhost:6379/0").connection_pool.make_connection()
        assert connection.socket_timeout == 5
        assert connection.socket_connect_timeout == 5

    def test_the_default_client_would_have_been_broken(self):
        """Documents the regression: redis-py's default read timeout is 5s."""
        import redis.asyncio as aioredis

        from app.jobs.queue import RESERVE_BLOCK_SECONDS

        connection = aioredis.from_url(
            "redis://localhost:6379/0", decode_responses=True
        ).connection_pool.make_connection()
        assert connection.socket_timeout is not None
        assert connection.socket_timeout <= RESERVE_BLOCK_SECONDS, (
            "this is exactly why the idle poll raised TimeoutError"
        )


@requires_redis
class TestAgainstRealRedis:
    @pytest.mark.asyncio
    async def test_empty_queue_stays_idle_without_error(self, redis_url):
        client = build_worker_redis(redis_url)
        queue = AnalysisQueue(client)
        try:
            await client.delete(QUEUE_KEY)
            started = time.perf_counter()
            job = await queue.reserve(make_worker_id(), timeout=1)
            elapsed = time.perf_counter() - started
            assert job is None, "an empty queue must return None, not raise"
            assert 0.5 < elapsed < 4, f"should have blocked ~1s, took {elapsed:.2f}s"
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_repeated_idle_polls_never_raise(self, redis_url):
        """The original bug fired on essentially every poll."""
        client = build_worker_redis(redis_url)
        queue = AnalysisQueue(client)
        try:
            await client.delete(QUEUE_KEY)
            for _ in range(3):
                assert await queue.reserve(make_worker_id(), timeout=1) is None
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_queued_job_is_reserved(self, redis_url):
        client = build_worker_redis(redis_url)
        queue = AnalysisQueue(client)
        worker_id = make_worker_id()
        try:
            await client.delete(QUEUE_KEY)
            assert await queue.enqueue(
                analysis_id="a1", org_id="o1", document_id="d1", policy_id="p1"
            )
            started = time.perf_counter()
            job = await queue.reserve(worker_id, timeout=5)
            assert job is not None
            assert job.analysis_id == "a1"
            assert job.org_id == "o1"
            assert time.perf_counter() - started < 1, "a waiting job must return immediately"
            await queue.acknowledge(worker_id, job)
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_duplicate_enqueue_is_suppressed(self, redis_url):
        client = build_worker_redis(redis_url)
        queue = AnalysisQueue(client)
        try:
            await client.delete(QUEUE_KEY)
            await client.delete("clauseguard:analyses:enqueued:dup1")
            first = await queue.enqueue(
                analysis_id="dup1", org_id="o", document_id="d", policy_id="p"
            )
            second = await queue.enqueue(
                analysis_id="dup1", org_id="o", document_id="d", policy_id="p"
            )
            assert first is True and second is False
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_orphaned_job_is_recovered(self, redis_url):
        client = build_worker_redis(redis_url)
        queue = AnalysisQueue(client)
        dead_worker = make_worker_id()
        try:
            await client.delete(QUEUE_KEY)
            await queue.enqueue(analysis_id="a9", org_id="o", document_id="d", policy_id="p")
            job = await queue.reserve(dead_worker, timeout=5)
            assert job is not None  # reserved, then the worker "dies"
            assert await queue.requeue_stalled(dead_worker) == 1
            assert await queue.depth() == 1
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_health_reports_connected(self, redis_url):
        client = build_worker_redis(redis_url)
        try:
            health = await AnalysisQueue(client).health()
            assert health["connected"] is True
            assert health["queue_depth"] >= 0
        finally:
            await client.aclose()


class TestGenuineFailuresAreSurfaced:
    """Idle must be silent, but a real outage must not be swallowed."""

    @pytest.mark.asyncio
    async def test_connection_error_propagates_from_reserve(self):
        class DeadRedis:
            async def brpoplpush(self, *_a, **_kw):
                raise redis_exceptions.ConnectionError("connection refused")

        with pytest.raises(redis_exceptions.ConnectionError):
            await AnalysisQueue(DeadRedis()).reserve("w1", timeout=1)

    @pytest.mark.asyncio
    async def test_only_timeouts_are_treated_as_idle(self):
        class TimingOutRedis:
            async def brpoplpush(self, *_a, **_kw):
                raise redis_exceptions.TimeoutError("read timed out")

        assert await AnalysisQueue(TimingOutRedis()).reserve("w1", timeout=1) is None

    @pytest.mark.asyncio
    async def test_asyncio_timeout_is_also_idle(self):
        class TimingOutRedis:
            async def brpoplpush(self, *_a, **_kw):
                raise asyncio.TimeoutError  # noqa: UP041

        assert await AnalysisQueue(TimingOutRedis()).reserve("w1", timeout=1) is None

    @pytest.mark.asyncio
    async def test_health_reports_a_dead_server(self):
        class DeadRedis:
            async def ping(self):
                raise redis_exceptions.ConnectionError("connection refused")

        health = await AnalysisQueue(DeadRedis()).health()
        assert health["connected"] is False
        assert "ConnectionError" in health["error"]

    @pytest.mark.asyncio
    async def test_unreachable_server_fails_rather_than_hanging(self):
        """socket_connect_timeout must stay bounded even with no read deadline."""
        client = build_worker_redis("redis://127.0.0.1:1/0")  # nothing listens on port 1
        started = time.perf_counter()
        with pytest.raises((redis_exceptions.ConnectionError, OSError)):
            await client.ping()
        assert time.perf_counter() - started < 10
        await client.aclose()


class TestWorkerLoop:
    """The consume loop: idle is silent, outages back off, shutdown is clean."""

    @staticmethod
    def build_worker():
        import app.worker as worker_module

        worker = object.__new__(worker_module.Worker)
        worker.running = True
        worker.worker_id = "test-worker"
        return worker

    @pytest.mark.asyncio
    async def test_idle_queue_logs_nothing_and_keeps_running(self, caplog):
        import app.worker as worker_module

        worker = self.build_worker()
        polls = {"n": 0}

        class IdleQueue:
            async def heartbeat(self, _worker_id):
                return None

            async def reserve(self, _worker_id):
                polls["n"] += 1
                if polls["n"] >= 5:
                    worker.running = False
                return None  # always idle

        worker.queue = IdleQueue()
        with caplog.at_level("WARNING"):
            await worker_module.Worker._consume(worker)

        assert polls["n"] == 5
        assert not caplog.records, f"idle polling must be silent, got: {caplog.records}"

    @pytest.mark.asyncio
    async def test_reserved_job_is_processed(self):
        import app.worker as worker_module

        worker = self.build_worker()
        processed = []

        job = Job(
            analysis_id="a1",
            org_id="o1",
            document_id="d1",
            policy_id="p1",
            enqueued_at=time.time(),
        )

        class OneJobQueue:
            def __init__(self):
                self.served = False

            async def heartbeat(self, _worker_id):
                return None

            async def reserve(self, _worker_id):
                if self.served:
                    worker.running = False
                    return None
                self.served = True
                return job

        async def fake_process(self, incoming):
            processed.append(incoming)

        worker.queue = OneJobQueue()
        worker._process = fake_process.__get__(worker)
        await worker_module.Worker._consume(worker)
        assert [j.analysis_id for j in processed] == ["a1"]

    @pytest.mark.asyncio
    async def test_connection_failure_is_logged_and_backed_off(self, caplog, monkeypatch):
        import app.worker as worker_module

        monkeypatch.setattr(worker_module, "MAX_BACKOFF_SECONDS", 0)
        worker = self.build_worker()
        attempts = {"n": 0}

        class DeadQueue:
            async def heartbeat(self, _worker_id):
                attempts["n"] += 1
                if attempts["n"] >= 3:
                    worker.running = False
                raise redis_exceptions.ConnectionError("connection refused")

            async def reserve(self, _worker_id):
                raise AssertionError("should not be reached")

        slept = []
        real_sleep = asyncio.sleep

        async def record_sleep(delay):
            slept.append(delay)
            await real_sleep(0)

        monkeypatch.setattr(worker_module.asyncio, "sleep", record_sleep)
        worker.queue = DeadQueue()
        with caplog.at_level("ERROR"):
            await worker_module.Worker._consume(worker)

        assert attempts["n"] == 3
        assert any("redis unavailable" in r.message for r in caplog.records)
        assert slept, "a genuine outage must back off rather than spin"

    @pytest.mark.asyncio
    async def test_recovery_after_an_outage_is_logged(self, caplog, monkeypatch):
        import app.worker as worker_module

        monkeypatch.setattr(worker_module, "MAX_BACKOFF_SECONDS", 0)
        worker = self.build_worker()
        calls = {"n": 0}

        class FlakyQueue:
            async def heartbeat(self, _worker_id):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise redis_exceptions.ConnectionError("down")
                if calls["n"] >= 3:
                    worker.running = False

            async def reserve(self, _worker_id):
                return None

        async def instant(_delay):
            return None

        monkeypatch.setattr(worker_module.asyncio, "sleep", instant)
        worker.queue = FlakyQueue()
        with caplog.at_level("INFO"):
            await worker_module.Worker._consume(worker)
        assert any("redis recovered" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_stop_shuts_the_loop_down_cleanly(self):
        import app.worker as worker_module

        worker = self.build_worker()

        class IdleQueue:
            async def heartbeat(self, _worker_id):
                return None

            async def reserve(self, _worker_id):
                worker_module.Worker.stop(worker)  # request shutdown mid-poll
                return None

        worker.queue = IdleQueue()
        await asyncio.wait_for(worker_module.Worker._consume(worker), timeout=5)
        assert worker.running is False

    @pytest.mark.asyncio
    async def test_cancellation_propagates(self):
        import app.worker as worker_module

        worker = self.build_worker()

        class HangingQueue:
            async def heartbeat(self, _worker_id):
                return None

            async def reserve(self, _worker_id):
                raise asyncio.CancelledError

        worker.queue = HangingQueue()
        with pytest.raises(asyncio.CancelledError):
            await worker_module.Worker._consume(worker)
