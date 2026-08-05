'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiClientError, api } from '@/lib/api';
import { formatBytes, formatDate } from '@/lib/format';
import type { ReportStatus } from '@/lib/types';

import { useToast } from './Toast';
import { Badge, Button, Card, InfoTip, Skeleton } from './ui';

const POLL_MS = 4000;
const MAX_POLLS = 30; // ~2 minutes, then stop asking

/**
 * Report download and email-delivery state.
 *
 * The report is generated automatically by the worker once the analysis
 * finishes, so this polls until it becomes available. Download never waits for
 * the email: the two are independent.
 */
export function ReportPanel({
  analysisId,
  analysisStatus,
  documentTitle,
}: {
  analysisId: string;
  analysisStatus: string;
  documentTitle: string;
}) {
  const toast = useToast();
  const [status, setStatus] = useState<ReportStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState(false);
  const [resending, setResending] = useState(false);
  const pollsRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
  }, []);

  const refresh = useCallback(async () => {
    try {
      const next = await api.getReportStatus(analysisId);
      setStatus(next);
      pollsRef.current += 1;
      const settled =
        next.report_available &&
        (next.email_status === null ||
          ['sent', 'failed', 'permanently_failed'].includes(next.email_status));
      if (settled || next.generation_status === 'failed' || pollsRef.current >= MAX_POLLS) {
        stopPolling();
      }
    } catch {
      // Non-fatal: the panel simply keeps showing its previous state.
      stopPolling();
    } finally {
      setLoading(false);
    }
  }, [analysisId, stopPolling]);

  useEffect(() => {
    if (!['complete', 'partial'].includes(analysisStatus)) {
      setLoading(false);
      return;
    }
    void refresh();
    timerRef.current = setInterval(() => void refresh(), POLL_MS);
    return stopPolling;
  }, [analysisStatus, refresh, stopPolling]);

  async function download() {
    setDownloading(true);
    try {
      const { blob, filename } = await api.downloadReport(analysisId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(url);
      toast.success('Report downloaded.');
    } catch (err) {
      const message =
        err instanceof ApiClientError && err.code === 'REPORT_NOT_READY'
          ? 'The report is still being generated. Try again in a moment.'
          : err instanceof Error
            ? err.message
            : 'The report could not be downloaded.';
      toast.error(message);
    } finally {
      setDownloading(false);
    }
  }

  async function resend() {
    setResending(true);
    try {
      const next = await api.resendReportEmail(analysisId);
      setStatus(next);
      toast.success(
        next.email_status === 'sent'
          ? `Report emailed to ${next.email_masked_recipient ?? 'your sign-in address'}.`
          : 'Email queued.',
      );
    } catch (err) {
      const message =
        err instanceof ApiClientError && err.status === 429
          ? 'Too many report emails. Please wait before trying again.'
          : err instanceof Error
            ? err.message
            : 'The report could not be emailed.';
      toast.error(message);
    } finally {
      setResending(false);
    }
  }

  if (!['complete', 'partial'].includes(analysisStatus)) return null;

  if (loading && !status) {
    return (
      <Card>
        <Skeleton className="h-4 w-32" />
        <Skeleton className="mt-3 h-9 w-40" />
      </Card>
    );
  }

  const generating =
    !status?.report_available && status?.generation_status !== 'failed';
  const emailStatus = status?.email_status ?? null;

  return (
    <Card className="print-break-inside-avoid">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="flex items-center text-sm font-semibold text-slate-900 dark:text-slate-100">
            PDF report
            <InfoTip label="PDF report">
              Generated automatically when the analysis finishes, stored privately, and emailed to
              the address you signed in with. Quarantined findings appear in a separate section and
              are excluded from the score.
            </InfoTip>
          </h2>

          {generating ? (
            <p className="mt-1 flex items-center gap-2 text-sm text-slate-500 dark:text-slate-400">
              <span
                className="h-3 w-3 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600 dark:border-slate-700 dark:border-t-slate-300"
                aria-hidden="true"
              />
              Generating your report…
            </p>
          ) : status?.generation_status === 'failed' ? (
            <p className="mt-1 text-sm text-red-700 dark:text-red-400">
              The report could not be generated. Your analysis results above are unaffected.
            </p>
          ) : (
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
              {documentTitle}
              {status?.file_size ? ` · ${formatBytes(status.file_size)}` : ''}
              {status?.generated_at ? ` · ${formatDate(status.generated_at)}` : ''}
            </p>
          )}

          <div className="mt-3 flex flex-wrap items-center gap-2" aria-live="polite">
            {emailStatus === 'sent' ? (
              <>
                <Badge tone="success">✓ emailed</Badge>
                <span className="text-xs text-slate-500 dark:text-slate-400">
                  Report emailed to your sign-in address
                  {status?.email_masked_recipient ? ` (${status.email_masked_recipient})` : ''}.
                </span>
              </>
            ) : null}
            {emailStatus === 'pending' || emailStatus === 'sending' ? (
              <Badge tone="neutral">email sending…</Badge>
            ) : null}
            {emailStatus === 'failed' || emailStatus === 'permanently_failed' ? (
              <>
                <Badge tone="danger">email failed</Badge>
                <span className="text-xs text-red-700 dark:text-red-400">
                  {status?.email_error ?? 'Delivery failed.'} You can still download the report, or
                  try emailing it again.
                </span>
              </>
            ) : null}
            {status?.report_available && emailStatus === null ? (
              <span className="text-xs text-slate-500 dark:text-slate-400">
                No email has been sent for this report yet.
              </span>
            ) : null}
          </div>
        </div>

        <div className="no-print flex flex-wrap gap-2">
          <Button
            onClick={download}
            disabled={downloading || !status?.report_available}
            title={
              status?.report_available
                ? 'Download the PDF report'
                : 'The report is still being generated'
            }
          >
            {downloading ? 'Preparing…' : 'Download report'}
          </Button>
          {status?.can_resend ? (
            <Button variant="secondary" onClick={resend} disabled={resending}>
              {resending ? 'Sending…' : 'Email report again'}
            </Button>
          ) : null}
        </div>
      </div>
    </Card>
  );
}
