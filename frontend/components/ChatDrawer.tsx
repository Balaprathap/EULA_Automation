'use client';

import {
  type FormEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { Button, Spinner } from '@/components/ui';
import { api } from '@/lib/api';
import type {
  ChatCitation,
  ChatHistoryMessage,
} from '@/lib/types';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  citations?: ChatCitation[];
  model?: string;
  totalTokens?: number;
}

interface ChatDrawerProps {
  documentId: string;
  documentTitle: string;
  onSelectFinding?: (findingId: string) => void;
}

const SUGGESTIONS = [
  'What are the biggest risks in this agreement?',
  'What should a human review first?',
  'Can the vendor keep or use my data after termination?',
  'Explain the highest-risk clauses in simple English.',
];

function messageId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function citationTitle(citation: ChatCitation) {
  if (citation.type === 'finding') {
    return citation.category
      ? citation.category.replaceAll('_', ' ')
      : 'Verified finding';
  }

  if (citation.heading) return citation.heading;

  return citation.ordinal !== null
    ? `Document chunk ${citation.ordinal}`
    : 'Document source';
}

function AnswerText({
  text,
  citations,
  onCitation,
}: {
  text: string;
  citations: ChatCitation[];
  onCitation: (citation: ChatCitation) => void;
}) {
  const byRef = useMemo(
    () => new Map(citations.map((citation) => [citation.ref, citation])),
    [citations],
  );

  const parts = text.split(/(\[(?:C|F)\d+\])/g);

  return (
    <p className="whitespace-pre-wrap text-sm leading-6 text-slate-800 dark:text-slate-200">
      {parts.map((part, index) => {
        const match = /^\[((?:C|F)\d+)\]$/.exec(part);
        const citation = match ? byRef.get(match[1]) : undefined;

        if (!citation) return <span key={index}>{part}</span>;

        return (
          <button
            key={`${citation.ref}-${index}`}
            type="button"
            onClick={() => onCitation(citation)}
            className="mx-0.5 inline-flex rounded bg-slate-200 px-1.5 py-0.5
              text-xs font-semibold text-slate-800 hover:bg-slate-300
              dark:bg-slate-700 dark:text-slate-100 dark:hover:bg-slate-600"
            title={`View source ${citation.ref}`}
          >
            [{citation.ref}]
          </button>
        );
      })}
    </p>
  );
}

export function ChatDrawer({
  documentId,
  documentTitle,
  onSelectFinding,
}: ChatDrawerProps) {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const bottomRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (!open) return;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };

    window.addEventListener('keydown', onKeyDown);

    window.setTimeout(() => textareaRef.current?.focus(), 50);

    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open]);

  useEffect(() => {
    if (open) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, sending, open]);

  function selectCitation(citation: ChatCitation) {
    if (citation.finding_id && onSelectFinding) {
      onSelectFinding(citation.finding_id);
      setOpen(false);
    }
  }

  async function sendQuestion(question: string) {
    const trimmed = question.trim();

    if (!trimmed || sending) return;

    const history: ChatHistoryMessage[] = messages
      .slice(-8)
      .map((message) => ({
        role: message.role,
        content: message.content,
      }));

    const userMessage: ChatMessage = {
      id: messageId(),
      role: 'user',
      content: trimmed,
    };

    setMessages((current) => [...current, userMessage]);
    setInput('');
    setSending(true);
    setError(null);

    try {
      const response = await api.askChat({
        document_id: documentId,
        message: trimmed,
        history,
      });

      setMessages((current) => [
        ...current,
        {
          id: messageId(),
          role: 'assistant',
          content: response.answer,
          citations: response.citations,
          model: response.model,
          totalTokens: response.usage.total_tokens,
        },
      ]);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : 'ClauseGuard Assistant could not answer that question.',
      );
    } finally {
      setSending(false);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void sendQuestion(input);
  }

  return (
    <>
      <Button onClick={() => setOpen(true)}>
        Ask ClauseGuard ✦
      </Button>

      {open ? (
        <div className="no-print fixed inset-0 z-50">
          <button
            type="button"
            aria-label="Close ClauseGuard Assistant"
            onClick={() => setOpen(false)}
            className="absolute inset-0 bg-slate-950/40 backdrop-blur-[1px]"
          />

          <aside
            role="dialog"
            aria-modal="true"
            aria-label="ClauseGuard Assistant"
            className="absolute inset-y-0 right-0 flex w-full flex-col
              border-l border-slate-200 bg-white shadow-2xl
              sm:max-w-xl
              dark:border-slate-800 dark:bg-slate-950"
          >
            <header
              className="flex items-start justify-between gap-4 border-b
                border-slate-200 px-5 py-4 dark:border-slate-800"
            >
              <div>
                <div className="flex items-center gap-2">
                  <span
                    className="flex h-8 w-8 items-center justify-center
                      rounded-lg bg-slate-900 text-sm text-white
                      dark:bg-slate-100 dark:text-slate-900"
                    aria-hidden="true"
                  >
                    ✦
                  </span>

                  <div>
                    <h2 className="font-semibold text-slate-900 dark:text-slate-100">
                      Ask ClauseGuard
                    </h2>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      Grounded in this agreement
                    </p>
                  </div>
                </div>

                <p
                  className="mt-2 max-w-md truncate text-xs
                    text-slate-500 dark:text-slate-400"
                  title={documentTitle}
                >
                  {documentTitle}
                </p>
              </div>

              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close assistant"
                className="rounded-md px-2 py-1 text-xl text-slate-500
                  hover:bg-slate-100 hover:text-slate-900
                  dark:hover:bg-slate-800 dark:hover:text-slate-100"
              >
                ×
              </button>
            </header>

            <div className="flex-1 overflow-y-auto px-5 py-5">
              {messages.length === 0 ? (
                <div className="space-y-5">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                      Ask about this agreement
                    </h3>

                    <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                      Answers use contract text and verified ClauseGuard findings.
                    </p>
                  </div>

                  <div className="grid gap-2">
                    {SUGGESTIONS.map((suggestion) => (
                      <button
                        key={suggestion}
                        type="button"
                        disabled={sending}
                        onClick={() => void sendQuestion(suggestion)}
                        className="rounded-lg border border-slate-200 bg-slate-50
                          px-3 py-3 text-left text-sm text-slate-700
                          transition hover:border-slate-300 hover:bg-slate-100
                          disabled:opacity-60
                          dark:border-slate-800 dark:bg-slate-900
                          dark:text-slate-300 dark:hover:bg-slate-800"
                      >
                        {suggestion}
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="space-y-5">
                  {messages.map((message) => (
                    <div
                      key={message.id}
                      className={
                        message.role === 'user'
                          ? 'flex justify-end'
                          : 'flex justify-start'
                      }
                    >
                      {message.role === 'user' ? (
                        <div
                          className="max-w-[85%] rounded-2xl rounded-br-md
                            bg-slate-900 px-4 py-3 text-sm text-white
                            dark:bg-slate-100 dark:text-slate-900"
                        >
                          {message.content}
                        </div>
                      ) : (
                        <div className="w-full space-y-3">
                          <div
                            className="rounded-2xl rounded-bl-md border
                              border-slate-200 bg-slate-50 px-4 py-3
                              dark:border-slate-800 dark:bg-slate-900"
                          >
                            <AnswerText
                              text={message.content}
                              citations={message.citations ?? []}
                              onCitation={selectCitation}
                            />
                          </div>

                          {message.citations?.length ? (
                            <div className="space-y-2">
                              <p
                                className="text-[11px] font-semibold uppercase
                                  tracking-wide text-slate-500 dark:text-slate-400"
                              >
                                Sources
                              </p>

                              {message.citations.map((citation) => {
                                const clickable =
                                  Boolean(citation.finding_id) &&
                                  Boolean(onSelectFinding);

                                return (
                                  <button
                                    key={citation.ref}
                                    type="button"
                                    disabled={!clickable}
                                    onClick={() => selectCitation(citation)}
                                    className="block w-full rounded-lg border
                                      border-slate-200 px-3 py-2 text-left
                                      disabled:cursor-default
                                      dark:border-slate-800"
                                  >
                                    <div className="flex items-center gap-2">
                                      <span
                                        className="rounded bg-slate-100 px-1.5 py-0.5
                                          text-xs font-semibold text-slate-700
                                          dark:bg-slate-800 dark:text-slate-200"
                                      >
                                        {citation.ref}
                                      </span>

                                      <span
                                        className="truncate text-xs font-medium
                                          capitalize text-slate-700
                                          dark:text-slate-300"
                                      >
                                        {citationTitle(citation)}
                                      </span>
                                    </div>

                                    {citation.quote ? (
                                      <p
                                        className="mt-1 line-clamp-2 text-xs
                                          text-slate-500 dark:text-slate-400"
                                      >
                                        “{citation.quote}”
                                      </p>
                                    ) : null}
                                  </button>
                                );
                              })}
                            </div>
                          ) : null}

                          {message.model ? (
                            <p className="text-[10px] text-slate-400 dark:text-slate-600">
                              {message.model}
                              {message.totalTokens
                                ? ` · ${message.totalTokens.toLocaleString()} tokens`
                                : ''}
                            </p>
                          ) : null}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {sending ? (
                <div className="mt-5">
                  <Spinner label="ClauseGuard is reviewing the evidence…" />
                </div>
              ) : null}

              {error ? (
                <div
                  role="alert"
                  className="mt-4 rounded-lg border border-red-200 bg-red-50
                    p-3 text-sm text-red-700
                    dark:border-red-900 dark:bg-red-950/40 dark:text-red-300"
                >
                  {error}
                </div>
              ) : null}

              <div ref={bottomRef} />
            </div>

            <footer
              className="border-t border-slate-200 bg-white p-4
                dark:border-slate-800 dark:bg-slate-950"
            >
              <form onSubmit={submit}>
                <div
                  className="flex items-end gap-2 rounded-xl border
                    border-slate-300 bg-white p-2
                    focus-within:ring-2 focus-within:ring-slate-900
                    dark:border-slate-700 dark:bg-slate-900
                    dark:focus-within:ring-slate-100"
                >
                  <textarea
                    ref={textareaRef}
                    value={input}
                    maxLength={2000}
                    rows={2}
                    disabled={sending}
                    placeholder="Ask about this agreement…"
                    onChange={(event) => setInput(event.target.value)}
                    onKeyDown={(event) => {
                      if (
                        event.key === 'Enter' &&
                        !event.shiftKey &&
                        !event.nativeEvent.isComposing
                      ) {
                        event.preventDefault();
                        void sendQuestion(input);
                      }
                    }}
                    className="max-h-32 min-h-[44px] flex-1 resize-none
                      bg-transparent px-2 py-2 text-sm text-slate-900
                      outline-none placeholder:text-slate-400
                      disabled:opacity-60
                      dark:text-slate-100 dark:placeholder:text-slate-500"
                  />

                  <Button
                    type="submit"
                    disabled={sending || input.trim().length < 2}
                    ariaLabel="Send question"
                  >
                    Send
                  </Button>
                </div>

                <div
                  className="mt-2 flex items-center justify-between
                    text-[10px] text-slate-400"
                >
                  <span>Enter to send · Shift+Enter for a new line</span>
                  <span>{input.length}/2000</span>
                </div>

                <p className="mt-2 text-[10px] text-slate-400">
                  AI-generated compliance assistance. Not legal advice.
                </p>
              </form>
            </footer>
          </aside>
        </div>
      ) : null}
    </>
  );
}
