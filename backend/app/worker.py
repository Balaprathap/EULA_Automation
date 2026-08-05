"""Analysis worker.

Runs from the same Docker image as the API with a different entrypoint. Loops
on the Redis queue, claims each job atomically in Postgres, heartbeats while it
works, and recovers anything a dead worker left behind.
"""

from __future__ import annotations

import asyncio
import signal
import time
from typing import Any

from app.core.config import get_settings
from app.core.logging import analysis_id_var, configure_logging, get_logger, org_id_var
from app.db.repositories.action_items import ActionItemRepository
from app.db.repositories.analyses import AnalysisRepository, FindingRepository
from app.db.repositories.documents import ChunkRepository, DocumentRepository
from app.db.repositories.policies import PolicyRepository
from app.db.repositories.reports import DeliveryRepository, ReportRepository
from app.db.session import close_pool, init_pool
from app.jobs.queue import AnalysisQueue, Job, build_worker_redis, make_worker_id
from app.providers.embedding.factory import build_embedding_provider
from app.providers.llm.factory import build_llm_provider
from app.services.audit import AuditAction, record_audit
from app.services.pipeline import AnalysisPipeline
from app.services.retrieval import HybridRetriever
from app.services.tools import ToolExecutor

logger = get_logger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 15
RECOVERY_INTERVAL_SECONDS = 60
MAX_BACKOFF_SECONDS = 30


class Worker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.worker_id = make_worker_id()
        self.running = True
        self.redis: Any = None
        self.queue: AnalysisQueue | None = None

        self.documents = DocumentRepository()
        self.chunks = ChunkRepository()
        self.policies = PolicyRepository()
        self.analyses = AnalysisRepository()
        self.findings = FindingRepository()
        self.reports = ReportRepository()
        self.deliveries = DeliveryRepository()
        self.action_items = ActionItemRepository()

    async def start(self) -> None:
        configure_logging(self.settings.log_level)
        logger.info("worker starting", extra={"worker_id": self.worker_id})

        await init_pool(self.settings.database_url, min_size=1, max_size=5)

        # socket_timeout=None: a blocking BRPOPLPUSH must not have a read
        # deadline shorter than the block itself. See build_worker_redis().
        self.redis = build_worker_redis(self.settings.redis_url)
        await self.redis.ping()
        self.queue = AnalysisQueue(self.redis)

        # Anything this worker id left behind on a previous life.
        await self.queue.requeue_stalled(self.worker_id)

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                asyncio.get_running_loop().add_signal_handler(sig, self.stop)
            except NotImplementedError:  # Windows
                signal.signal(sig, lambda *_: self.stop())

        await asyncio.gather(self._consume(), self._recover_periodically())

    def stop(self) -> None:
        logger.info("worker stopping; finishing current job")
        self.running = False

    async def _recover_periodically(self) -> None:
        """Requeue analyses whose worker stopped heartbeating."""
        while self.running:
            await asyncio.sleep(RECOVERY_INTERVAL_SECONDS)
            try:
                stalled = await self.analyses.recover_stalled(older_than_minutes=5)
                for row in stalled:
                    if self.queue:
                        await self.queue.enqueue(
                            analysis_id=str(row["id"]),
                            org_id=str(row["org_id"]),
                            document_id=str(row["document_id"]),
                            policy_id=str(row["policy_id"]),
                        )
                if stalled:
                    logger.warning("recovered stalled analyses", extra={"count": len(stalled)})
            except Exception as exc:  # noqa: BLE001
                logger.error("recovery sweep failed", extra={"error_type": type(exc).__name__})

    async def _consume(self) -> None:
        """Consume jobs until told to stop.

        An empty queue is normal: `reserve` returns None and the loop simply
        goes round again, silently. Genuine Redis failures are surfaced and
        backed off exponentially so an outage does not become a tight retry
        loop against a dead server.
        """
        import redis.exceptions as redis_exceptions

        assert self.queue is not None
        connection_failures = 0

        while self.running:
            try:
                await self.queue.heartbeat(self.worker_id)
                job = await self.queue.reserve(self.worker_id)

                # Reaching here means Redis is healthy, whether or not a job
                # was waiting.
                if connection_failures:
                    logger.info("redis recovered", extra={"after_failures": connection_failures})
                    connection_failures = 0

                if job is None:
                    continue  # idle - not an error, not logged
                await self._process(job)

            except asyncio.CancelledError:
                raise

            except (redis_exceptions.ConnectionError, OSError) as exc:
                # Genuine connectivity problem: surface it, then back off so a
                # sustained outage does not spin the CPU.
                connection_failures += 1
                delay = min(MAX_BACKOFF_SECONDS, 2 ** min(connection_failures, 5))
                logger.error(
                    "redis unavailable; retrying",
                    extra={
                        "error_type": type(exc).__name__,
                        "consecutive_failures": connection_failures,
                        "retry_in_s": delay,
                    },
                )
                await asyncio.sleep(delay)

            except Exception as exc:  # noqa: BLE001 - the loop must survive anything
                logger.exception("worker loop error", extra={"error_type": type(exc).__name__})
                await asyncio.sleep(2)

    async def _process(self, job: Job) -> None:
        assert self.queue is not None
        analysis_id = job.analysis_id
        analysis_id_var.set(analysis_id)
        org_id_var.set(job.org_id)
        started = time.perf_counter()

        claimed = await self.analyses.claim(analysis_id, self.worker_id)
        if claimed is None:
            # Another worker owns it, or it already finished.
            logger.info("job already claimed", extra={"analysis_id": analysis_id})
            await self.queue.acknowledge(self.worker_id, job)
            return

        heartbeat = asyncio.create_task(self._heartbeat(analysis_id))
        try:
            llm = build_llm_provider(self.settings)
            embeddings = build_embedding_provider(self.settings)
            retriever = HybridRetriever(
                self.chunks, embeddings, top_k=self.settings.retrieval_top_k
            )

            async def chunk_lookup(chunk_id: str):
                row = await self.chunks.get(chunk_id)
                if row is None:
                    return None
                return type(
                    "Chunk",
                    (),
                    {
                        "id": str(row["id"]),
                        "document_id": str(row["document_id"]),
                        "ordinal": row["ordinal"],
                    },
                )()

            pipeline = AnalysisPipeline(
                documents=self.documents,
                chunks=self.chunks,
                policies=self.policies,
                analyses=self.analyses,
                findings=self.findings,
                retriever=retriever,
                embeddings=embeddings,
                llm=llm,
                tool_executor=ToolExecutor(retriever, chunk_lookup),
                max_tool_calls=self.settings.max_tool_calls_per_category,
                top_k=self.settings.retrieval_top_k,
            )

            result = await pipeline.run(claimed, self.worker_id)

            await self.analyses.complete(
                analysis_id,
                status=result.status,
                stage="complete",
                overall_score=result.overall_score,
                risk_band=result.risk_band,
                finding_count=result.finding_count,
                review_count=result.review_count,
                quarantine_count=result.quarantine_count,
                verification_pass_rate=result.verification_pass_rate,
                executive_summary=result.executive_summary,
                degraded_retrieval=result.degraded_retrieval,
                degraded_reason=result.degraded_reason,
                stage_timings_ms=result.stage_timings_ms,
                model_used=result.model_used,
                progress_message=(
                    "Analysis complete."
                    if result.status == "complete"
                    else "Analysis finished with categories that need human review."
                ),
            )
            await record_audit(
                org_id=job.org_id,
                action=AuditAction.ANALYSIS_COMPLETE,
                resource_type="analysis",
                resource_id=analysis_id,
                metadata={
                    "status": result.status,
                    "findings": result.finding_count,
                    "quarantined": result.quarantine_count,
                    "score": result.overall_score,
                },
            )
            # Report generation and email delivery. Deliberately AFTER the
            # analysis has been finalised and audited, and wrapped so that
            # nothing here can change the analysis status. A failure is logged
            # and recorded on the report/delivery rows only.
            await self._deliver_report(claimed, result)

            # Deterministic action-item derivation. No AI call, no token cost,
            # and wrapped so it can never affect the analysis status.
            await self._derive_action_items(claimed)

            await self.queue.acknowledge(self.worker_id, job)
            logger.info(
                "analysis finished",
                extra={
                    "analysis_id": analysis_id,
                    "status": result.status,
                    "findings": result.finding_count,
                    "quarantined": result.quarantine_count,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "cost_usd": result.estimated_cost_usd,
                },
            )

        except Exception as exc:  # noqa: BLE001
            logger.exception("analysis failed", extra={"analysis_id": analysis_id})
            attempts = int(claimed.get("attempt_count") or 1)
            if attempts < 3:
                # Transient failure: put it back and let another attempt resume
                # from the categories already recorded as complete.
                await self.analyses.update_stage(
                    analysis_id,
                    claimed.get("stage") or "extracting",
                    message=f"Retrying after an error (attempt {attempts + 1} of 3).",
                )
                await self.queue.retry(self.worker_id, job)
            else:
                await self.analyses.fail(
                    analysis_id, "ANALYSIS_FAILED", f"{type(exc).__name__}: {exc}"
                )
                await self.queue.acknowledge(self.worker_id, job)
        finally:
            heartbeat.cancel()
            analysis_id_var.set("-")

    async def _derive_action_items(self, analysis: dict) -> None:
        """Derive action items from verified findings. Never fatal."""
        analysis_id = str(analysis["id"])
        org_id = str(analysis["org_id"])
        try:
            from app.services.action_items import derive_action_items

            rows = await self.findings.list_for_analysis(
                org_id, analysis_id, include_quarantined=False
            )
            derived = derive_action_items([dict(r) for r in rows])
            if not derived:
                return
            created = await self.action_items.bulk_upsert(
                org_id=org_id,
                analysis_id=analysis_id,
                document_id=str(analysis["document_id"]),
                items=[vars(item) for item in derived],
            )
            logger.info(
                "action items generated",
                extra={"analysis_id": analysis_id, "derived": len(derived), "created": created},
            )
        except Exception as exc:  # noqa: BLE001 - must never affect analysis status
            logger.error(
                "action item derivation failed; the analysis itself is unaffected",
                extra={"analysis_id": analysis_id, "error_type": type(exc).__name__},
            )

    async def _deliver_report(self, analysis: dict, result) -> None:
        """Generate the PDF report and email it to the requesting user.

        Every failure path here is swallowed: a completed analysis must never be
        turned into a failed one by a reporting or email problem.
        """
        analysis_id = str(analysis["id"])
        org_id = str(analysis["org_id"])
        try:
            from app.providers.email.providers import build_email_provider
            from app.providers.storage.factory import build_report_storage_provider
            from app.services.report_delivery import (
                ReportDeliveryService,
                build_report_context,
                resolve_recipient,
                severity_counts_from,
            )
            from app.services.report_storage import ProviderStorageAdapter

            # Re-read the finalised row so the report reflects persisted state.
            final = await self.analyses.get(org_id, analysis_id) or analysis

            context = await build_report_context(
                analyses_repo=self.analyses,
                documents_repo=self.documents,
                policies_repo=self.policies,
                findings_repo=self.findings,
                org_id=org_id,
                analysis=final,
            )

            service = ReportDeliveryService(
                reports=self.reports,
                deliveries=self.deliveries,
                storage=ProviderStorageAdapter(
                    build_report_storage_provider(self.settings),
                    analysis_id=analysis_id,
                    org_id=org_id,
                    version=1,
                ),
                email_provider=build_email_provider(self.settings),
                max_attachment_bytes=int(self.settings.email_max_attachment_mb * 1024 * 1024),
                max_attempts=self.settings.email_max_attempts,
                signed_url_ttl=self.settings.report_signed_url_ttl_seconds,
            )

            report = await service.generate_and_store(
                org_id=org_id, analysis_id=analysis_id, context=context, version=1
            )
            await record_audit(
                org_id=org_id,
                action=AuditAction.REPORT_GENERATE,
                resource_type="analysis_report",
                resource_id=str(report.get("id")),
                metadata={"analysis_id": analysis_id, "bytes": report.get("file_size")},
            )

            # Recipient resolved server-side from the profile that requested the
            # analysis. No client input is involved anywhere in this path.
            recipient, user_id = await resolve_recipient(
                str(final.get("requested_by")) if final.get("requested_by") else None
            )
            if not recipient:
                logger.info(
                    "no verified recipient; skipping email",
                    extra={"analysis_id": analysis_id},
                )
                return

            outcome = await service.send_report_email(
                org_id=org_id,
                analysis_id=analysis_id,
                report=report,
                recipient_email=recipient,
                recipient_user_id=user_id,
                document_title=str(context.document.get("title") or "Agreement"),
                analysis=dict(final),
                severity_counts=severity_counts_from(context.findings),
            )
            if outcome.email_attempted:
                await record_audit(
                    org_id=org_id,
                    action=AuditAction.REPORT_EMAIL_SEND,
                    resource_type="analysis_report",
                    resource_id=str(report.get("id")),
                    actor_id=user_id,
                    metadata={"analysis_id": analysis_id, "sent": outcome.email_sent},
                )
        except Exception as exc:  # noqa: BLE001 - must never affect analysis status
            logger.error(
                "report delivery failed; the analysis itself is unaffected",
                extra={"analysis_id": analysis_id, "error_type": type(exc).__name__},
            )

    async def _heartbeat(self, analysis_id: str) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                await self.analyses.heartbeat(analysis_id, self.worker_id)
                if self.queue:
                    await self.queue.heartbeat(self.worker_id)
        except asyncio.CancelledError:
            pass


async def main() -> None:
    worker = Worker()
    try:
        await worker.start()
    finally:
        await close_pool()
        if worker.redis is not None:
            await worker.redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
