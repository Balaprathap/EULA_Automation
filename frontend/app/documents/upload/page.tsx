'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

import { RequireAuth } from '@/components/RequireAuth';
import { useToast } from '@/components/Toast';
import {
  Badge,
  Breadcrumbs,
  Button,
  Card,
  ErrorState,
  Field,
  InfoTip,
  SectionHeading,
  Spinner,
  Warning,
  inputClasses,
} from '@/components/ui';
import { ApiClientError, api } from '@/lib/api';
import { formatBytes, formatDate } from '@/lib/format';
import type { Document, Policy } from '@/lib/types';

const ACCEPTED = '.pdf,.docx,.txt';
const MAX_MB = 10;
const MIN_PASTE_CHARS = 200;

function UploadWorkspace() {
  const router = useRouter();
  const toast = useToast();

  const [mode, setMode] = useState<'file' | 'paste'>('file');
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [title, setTitle] = useState('');
  const [vendor, setVendor] = useState('');
  const [pasted, setPasted] = useState('');
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [policyId, setPolicyId] = useState('');
  const [recent, setRecent] = useState<Document[]>([]);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);

  useEffect(() => {
    api
      .listPolicies()
      .then((list) => {
        setPolicies(list);
        setPolicyId(list.find((p) => p.is_default)?.id ?? list[0]?.id ?? '');
      })
      .catch(() => setPolicies([]));
    api
      .listDocuments({ limit: 5 })
      .then((page) => setRecent(page.items))
      .catch(() => setRecent([]));
  }, []);

  const chooseFile = useCallback(
    (candidate: File | null) => {
      setError(null);
      setHint(null);
      if (!candidate) {
        setFile(null);
        return;
      }
      if (candidate.size > MAX_MB * 1024 * 1024) {
        setError(
          `That file is ${formatBytes(candidate.size)}, which exceeds the ${MAX_MB} MB limit.`,
        );
        setFile(null);
        return;
      }
      if (!/\.(pdf|docx|txt)$/i.test(candidate.name)) {
        setError('Only PDF, DOCX, and TXT files are supported.');
        setFile(null);
        return;
      }
      setFile(candidate);
      if (!title) setTitle(candidate.name.replace(/\.[^.]+$/, ''));
    },
    [title],
  );

  async function submit() {
    setError(null);
    setHint(null);
    setBusy(true);
    try {
      setStage('Uploading and validating…');
      const document =
        mode === 'file' && file
          ? await api.uploadDocument(file, {
              vendor_name: vendor || undefined,
              title: title || undefined,
            })
          : await api.pasteDocument({
              title: title || 'Pasted agreement',
              text: pasted,
              vendor_name: vendor || undefined,
            });

      setStage('Queueing the analysis…');
      const analysis = await api.startAnalysis(document.id, policyId || undefined);
      toast.success('Analysis queued.');
      router.push(`/documents/${document.id}/analyses/${analysis.id}`);
    } catch (err) {
      if (err instanceof ApiClientError) {
        setError(err.message);
        if (err.code === 'SCANNED_PDF_UNSUPPORTED') {
          setHint(
            'This PDF contains images of text rather than selectable text. Open it in a PDF reader ' +
              'and try selecting a sentence — if you cannot, use the "Paste text" tab instead. ' +
              'ClauseGuard does not run OCR, because transcription errors would undermine the ' +
              'guarantee that every quote is verifiable against the source.',
          );
        } else if (err.code === 'ENCRYPTED_PDF_UNSUPPORTED') {
          setHint('Remove the password protection from the PDF and upload it again.');
        }
      } else {
        setError(err instanceof Error ? err.message : 'The upload failed.');
      }
      toast.error('Upload failed.');
    } finally {
      setBusy(false);
      setStage('');
    }
  }

  const pastedLength = pasted.trim().length;
  const canSubmit =
    !busy &&
    (mode === 'file'
      ? Boolean(file)
      : pastedLength >= MIN_PASTE_CHARS && title.trim().length > 0);

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <Breadcrumbs
        items={[
          { label: 'Dashboard', href: '/dashboard' },
          { label: 'Documents', href: '/documents' },
          { label: 'Add an agreement' },
        ]}
      />
      <SectionHeading
        title="Add an agreement"
        description="Upload a file or paste the text. Analysis starts automatically once it is ready."
      />

      <div
        className="flex gap-1 rounded-lg border border-slate-200 bg-white p-1 dark:border-slate-800 dark:bg-slate-900"
        role="tablist"
        aria-label="Input method"
      >
        {(['file', 'paste'] as const).map((option) => (
          <button
            key={option}
            role="tab"
            aria-selected={mode === option}
            onClick={() => {
              setMode(option);
              setError(null);
              setHint(null);
            }}
            className={`flex-1 rounded-md px-3 py-2 text-sm font-medium transition ${
              mode === option
                ? 'bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900'
                : 'text-slate-600 hover:bg-slate-50 dark:text-slate-400 dark:hover:bg-slate-800'
            }`}
          >
            {option === 'file' ? 'Upload a file' : 'Paste text'}
          </button>
        ))}
      </div>

      <Card className="space-y-4">
        {mode === 'file' ? (
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              chooseFile(e.dataTransfer.files?.[0] ?? null);
            }}
            className={`rounded-xl border-2 border-dashed p-8 text-center transition ${
              dragging
                ? 'border-slate-900 bg-slate-50 dark:border-slate-100 dark:bg-slate-800'
                : 'border-slate-300 dark:border-slate-700'
            }`}
          >
            <p className="text-sm font-medium text-slate-900 dark:text-slate-100">
              Drag a file here, or choose one
            </p>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              PDF, DOCX, or TXT · up to {MAX_MB} MB · up to 150 pages
            </p>
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              PDFs must contain selectable text — scans are rejected, not silently analyzed
              <InfoTip label="Why scans are rejected">
                Running OCR would introduce transcription errors into the evidence chain. Since
                ClauseGuard guarantees every quote can be located in the source, a scanned PDF is
                refused with an explanation instead.
              </InfoTip>
            </p>
            <label htmlFor="file-input" className="sr-only">
              Choose an agreement file
            </label>
            <input
              id="file-input"
              type="file"
              accept={ACCEPTED}
              onChange={(e) => chooseFile(e.target.files?.[0] ?? null)}
              className="mt-4 block w-full text-sm text-slate-600 file:mr-3 file:rounded-md
                file:border-0 file:bg-slate-900 file:px-4 file:py-2 file:text-sm file:text-white
                dark:text-slate-400 dark:file:bg-slate-100 dark:file:text-slate-900"
            />
            {file ? (
              <p className="mt-3 flex items-center justify-center gap-2 text-sm text-slate-700 dark:text-slate-300">
                <Badge tone="success">selected</Badge>
                <strong>{file.name}</strong>
                <span className="text-slate-500 dark:text-slate-400">
                  ({formatBytes(file.size)})
                </span>
              </p>
            ) : null}
          </div>
        ) : (
          <Field
            label="Agreement text"
            hint={`${pastedLength.toLocaleString()} characters — at least ${MIN_PASTE_CHARS} required`}
            error={
              pastedLength > 0 && pastedLength < MIN_PASTE_CHARS
                ? `Add at least ${MIN_PASTE_CHARS - pastedLength} more characters.`
                : undefined
            }
          >
            <textarea
              rows={14}
              value={pasted}
              onChange={(e) => setPasted(e.target.value)}
              placeholder="Paste the full text of the agreement here."
              className={`${inputClasses} font-mono text-xs`}
            />
          </Field>
        )}

        <Field label="Title">
          <input value={title} onChange={(e) => setTitle(e.target.value)} className={inputClasses} />
        </Field>

        <Field label="Vendor name" hint="Optional. Helps the model interpret defined terms.">
          <input
            value={vendor}
            onChange={(e) => setVendor(e.target.value)}
            className={inputClasses}
          />
        </Field>

        <Field
          label="Compliance policy"
          hint="Determines which categories are reviewed and how severity is weighted."
        >
          <select
            value={policyId}
            onChange={(e) => setPolicyId(e.target.value)}
            className={inputClasses}
          >
            {policies.length === 0 ? <option value="">No policies available</option> : null}
            {policies.map((policy) => (
              <option key={policy.id} value={policy.id}>
                {policy.name} (v{policy.version}) — {policy.rule_count} categories
                {policy.is_default ? ' — default' : ''}
              </option>
            ))}
          </select>
        </Field>

        {error ? <ErrorState error={error} /> : null}
        {hint ? <Warning title="What to do next">{hint}</Warning> : null}

        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={submit} disabled={!canSubmit}>
            {busy ? 'Working…' : 'Start analysis'}
          </Button>
          {busy ? <Spinner label={stage || 'Preparing the document'} /> : null}
        </div>
      </Card>

      {recent.length > 0 ? (
        <Card>
          <h2 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
            Recent uploads
          </h2>
          <ul className="mt-3 divide-y divide-slate-100 dark:divide-slate-800">
            {recent.map((document) => (
              <li key={document.id} className="py-2">
                <Link
                  href={`/documents/${document.id}`}
                  className="flex items-center justify-between gap-3 rounded px-1 py-1 text-sm hover:bg-slate-50 dark:hover:bg-slate-800"
                >
                  <span className="min-w-0 flex-1 truncate">{document.title}</span>
                  <Badge tone={document.status === 'ready' ? 'success' : 'neutral'}>
                    {document.status}
                  </Badge>
                  <time
                    dateTime={document.created_at}
                    className="hidden text-xs text-slate-500 sm:block dark:text-slate-400"
                  >
                    {formatDate(document.created_at)}
                  </time>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}

export default function UploadPage() {
  return (
    <RequireAuth>
      <UploadWorkspace />
    </RequireAuth>
  );
}
