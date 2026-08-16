'use client';

import Link from 'next/link';
import { useState } from 'react';

import { RequireAuth } from '@/components/RequireAuth';
import { useToast } from '@/components/Toast';
import {
  Breadcrumbs,
  Button,
  Card,
  ErrorState,
  InfoTip,
  Spinner,
  Warning,
  inputClasses,
} from '@/components/ui';
import { api } from '@/lib/api';
import type { PolicyAIDraft, PolicyRuleInput } from '@/lib/types';

const AGREEMENT_TYPES = [
  'SaaS Vendor Agreement',
  'Data Processing Agreement',
  'AI Provider Agreement',
  'Cloud Services Agreement',
  'Employment Agreement',
  'General Commercial Agreement',
];

const DEFAULT_PROMPT =
  'Focus on privacy, data use, AI training, data retention, third-party sharing, security, liability, termination, and data deletion.';

function PolicyBuilder() {
  const toast = useToast();

  const [agreementType, setAgreementType] = useState('SaaS Vendor Agreement');
  const [nameHint, setNameHint] = useState('');
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const [ruleCount, setRuleCount] = useState(8);

  const [draft, setDraft] = useState<PolicyAIDraft | null>(null);
  const [generating, setGenerating] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function generate() {
    if (prompt.trim().length < 10) {
      setError('Describe what the policy should review in at least 10 characters.');
      return;
    }

    setGenerating(true);
    setError(null);

    try {
      const result = await api.generatePolicyDraft({
        prompt: prompt.trim(),
        agreement_type: agreementType,
        name_hint: nameHint.trim() || undefined,
        rule_count: ruleCount,
      });

      setDraft(result);
      toast.success(
        `AI generated ${result.rules.length} categories. Review them before creating the policy.`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The AI policy draft could not be generated.');
    } finally {
      setGenerating(false);
    }
  }

  function updateDraft(patch: Partial<PolicyAIDraft>) {
    setDraft((current) => (current ? { ...current, ...patch } : current));
  }

  function updateRule(index: number, patch: Partial<PolicyRuleInput>) {
    setDraft((current) => {
      if (!current) return current;

      return {
        ...current,
        rules: current.rules.map((rule, i) =>
          i === index ? { ...rule, ...patch } : rule,
        ),
      };
    });
  }

  function removeRule(index: number) {
    setDraft((current) => {
      if (!current) return current;

      return {
        ...current,
        rules: current.rules
          .filter((_, i) => i !== index)
          .map((rule, i) => ({ ...rule, sort_order: i })),
      };
    });
  }

  function addRule() {
    setDraft((current) => {
      if (!current) return current;

      const next: PolicyRuleInput = {
        category: '',
        display_name: '',
        description: '',
        retrieval_guidance: '',
        keywords: [],
        severity_weight: 0.5,
        confidence_threshold: 0.35,
        escalate: false,
        is_enabled: true,
        sort_order: current.rules.length,
      };

      return {
        ...current,
        rules: [...current.rules, next],
      };
    });
  }

  const problems =
    draft?.rules.flatMap((rule, index) => {
      const issues: string[] = [];

      if (!/^[a-z][a-z0-9_]*$/.test(rule.category)) {
        issues.push(`Category ${index + 1}: use lowercase letters, numbers, and underscores.`);
      }

      if (!rule.display_name.trim()) {
        issues.push(`Category ${index + 1}: display name is required.`);
      }

      if (!rule.description.trim()) {
        issues.push(`Category ${index + 1}: description is required.`);
      }

      return issues;
    }) ?? [];

  const categories = draft?.rules.map((rule) => rule.category) ?? [];

  const duplicates = categories.filter(
    (category, index) => category && categories.indexOf(category) !== index,
  );

  async function createPolicy() {
    if (!draft) return;

    if (!draft.name.trim()) {
      setError('A policy name is required.');
      return;
    }

    if (draft.rules.length === 0 || problems.length > 0 || duplicates.length > 0) {
      setError('Fix the policy-category validation issues before creating the policy.');
      return;
    }

    setCreating(true);
    setError(null);

    try {
      const created = await api.createPolicy({
        name: draft.name.trim(),
        description: draft.description?.trim() || undefined,
        rules: draft.rules.map((rule, index) => ({
          ...rule,
          sort_order: index,
        })),
      });

      toast.success('Policy created successfully.');
      window.location.href = `/policies/${created.id}`;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'The policy could not be created.';
      setError(message);
      toast.error(message);
      setCreating(false);
    }
  }

  if (generating) {
    return (
      <div className="space-y-6">
        <Breadcrumbs
          items={[
            { label: 'Dashboard', href: '/dashboard' },
            { label: 'Policies', href: '/policies' },
            { label: 'AI Policy Builder' },
          ]}
        />

        <Card className="py-14">
          <Spinner label="Building your policy draft with AI" />
          <p className="mt-4 text-center text-sm text-slate-500 dark:text-slate-400">
            ClauseGuard is generating detection categories only. Nothing is being saved yet.
          </p>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Breadcrumbs
        items={[
          { label: 'Dashboard', href: '/dashboard' },
          { label: 'Policies', href: '/policies' },
          { label: 'AI Policy Builder' },
        ]}
      />

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold text-slate-900 dark:text-slate-100">
              AI Policy Builder
            </h1>
            <span className="rounded-full bg-violet-100 px-2 py-0.5 text-xs font-medium text-violet-700 dark:bg-violet-950 dark:text-violet-300">
              ✦ AI assisted
            </span>
          </div>

          <p className="mt-1 max-w-2xl text-sm text-slate-500 dark:text-slate-400">
            Describe what matters in your agreements. ClauseGuard will propose policy categories
            that you can review and edit before anything is saved.
          </p>
        </div>

        <Link href="/policies">
          <Button variant="ghost">Back to policies</Button>
        </Link>
      </div>

      <Warning title="AI creates a draft — you stay in control">
        The AI proposes category names, descriptions, retrieval guidance, and keywords. ClauseGuard
        assigns neutral scoring defaults. Review every category and scoring control before creating
        the policy.
      </Warning>

      {error ? <ErrorState error={error} /> : null}

      {!draft ? (
        <Card className="space-y-5">
          <div>
            <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
              What should this policy review?
            </h2>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
              Start with an agreement type, then describe the risks or obligations you care about.
            </p>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <label className="text-sm text-slate-700 dark:text-slate-300">
              Agreement type
              <select
                value={agreementType}
                onChange={(event) => setAgreementType(event.target.value)}
                className={`${inputClasses} mt-1`}
              >
                {AGREEMENT_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
            </label>

            <label className="text-sm text-slate-700 dark:text-slate-300">
              Policy name <span className="text-slate-400">(optional)</span>
              <input
                value={nameHint}
                onChange={(event) => setNameHint(event.target.value)}
                placeholder="e.g. SaaS Vendor Risk Policy"
                maxLength={200}
                className={`${inputClasses} mt-1`}
              />
            </label>
          </div>

          <label className="block text-sm text-slate-700 dark:text-slate-300">
            What should ClauseGuard look for?
            <textarea
              rows={6}
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              maxLength={4000}
              placeholder="Example: Do not allow customer information to be used for model training without explicit consent..."
              className={`${inputClasses} mt-1`}
            />
            <span className="mt-1 block text-xs text-slate-500">
              {prompt.length}/4000 characters
            </span>
          </label>

          <label className="block text-sm text-slate-700 dark:text-slate-300">
            Number of categories: <strong>{ruleCount}</strong>
            <input
              type="range"
              min={3}
              max={12}
              step={1}
              value={ruleCount}
              onChange={(event) => setRuleCount(Number(event.target.value))}
              className="mt-2 w-full"
            />
            <div className="flex justify-between text-xs text-slate-400">
              <span>3 focused</span>
              <span>12 comprehensive</span>
            </div>
          </label>

          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={() => void generate()}>
              Generate policy draft ✦
            </Button>

            <span className="text-xs text-slate-500 dark:text-slate-400">
              Nothing is persisted during generation.
            </span>
          </div>
        </Card>
      ) : (
        <>
          <Card className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-100">
                  Review AI draft
                </h2>
                <p className="text-sm text-slate-500 dark:text-slate-400">
                  Edit anything below before creating the policy.
                </p>
              </div>

              <Button variant="secondary" onClick={() => setDraft(null)}>
                Start over
              </Button>
            </div>

            <label className="block text-sm text-slate-700 dark:text-slate-300">
              Policy name
              <input
                value={draft.name}
                onChange={(event) => updateDraft({ name: event.target.value })}
                maxLength={200}
                className={`${inputClasses} mt-1`}
              />
            </label>

            <label className="block text-sm text-slate-700 dark:text-slate-300">
              Description
              <textarea
                rows={3}
                value={draft.description ?? ''}
                onChange={(event) => updateDraft({ description: event.target.value })}
                maxLength={2000}
                className={`${inputClasses} mt-1`}
              />
            </label>

            <div className="flex flex-wrap gap-4 text-xs text-slate-500 dark:text-slate-400">
              <span>Model: {draft.model}</span>
              <span>{draft.usage.total_tokens.toLocaleString()} tokens</span>
              <span>{draft.rules.length} categories</span>
              <span className="font-medium text-emerald-700 dark:text-emerald-400">
                Unsaved draft
              </span>
            </div>
          </Card>

          {problems.length > 0 ? (
            <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
              <p className="font-medium">Fix these fields before creating the policy:</p>
              <ul className="mt-1 list-inside list-disc">
                {problems.slice(0, 8).map((problem) => (
                  <li key={problem}>{problem}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {duplicates.length > 0 ? (
            <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
              Duplicate categories: {Array.from(new Set(duplicates)).join(', ')}
            </div>
          ) : null}

          <div className="space-y-4">
            {draft.rules.map((rule, index) => (
              <Card key={`${rule.category}-${index}`} className="space-y-4">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="flex h-7 w-7 items-center justify-center rounded-full bg-slate-100 text-xs font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                    {index + 1}
                  </span>

                  <input
                    value={rule.display_name}
                    onChange={(event) =>
                      updateRule(index, { display_name: event.target.value })
                    }
                    placeholder="Display name"
                    className="min-w-48 flex-1 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                  />

                  <input
                    value={rule.category}
                    onChange={(event) =>
                      updateRule(index, { category: event.target.value })
                    }
                    placeholder="category_key"
                    className="w-56 rounded-md border border-slate-300 bg-white px-3 py-2 font-mono text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                  />

                  <button
                    type="button"
                    onClick={() => removeRule(index)}
                    className="text-xs font-medium text-red-600 hover:underline dark:text-red-400"
                  >
                    Remove
                  </button>
                </div>

                <textarea
                  rows={2}
                  value={rule.description}
                  onChange={(event) =>
                    updateRule(index, { description: event.target.value })
                  }
                  placeholder="What this category detects"
                  className={`${inputClasses} mt-0`}
                />

                <textarea
                  rows={2}
                  value={rule.retrieval_guidance ?? ''}
                  onChange={(event) =>
                    updateRule(index, { retrieval_guidance: event.target.value })
                  }
                  placeholder="Retrieval guidance"
                  className={`${inputClasses} mt-0`}
                />

                <input
                  value={rule.keywords.join(', ')}
                  onChange={(event) =>
                    updateRule(index, {
                      keywords: event.target.value
                        .split(',')
                        .map((keyword) => keyword.trim())
                        .filter(Boolean),
                    })
                  }
                  placeholder="Keywords, comma separated"
                  className={`${inputClasses} mt-0`}
                />

                <div className="grid gap-4 sm:grid-cols-3">
                  <label className="text-xs text-slate-600 dark:text-slate-400">
                    Severity weight: <strong>{rule.severity_weight.toFixed(2)}</strong>
                    <InfoTip label="Severity weight">
                      Human-controlled scoring input. The AI Policy Builder does not choose this
                      value.
                    </InfoTip>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={rule.severity_weight}
                      onChange={(event) =>
                        updateRule(index, {
                          severity_weight: Number(event.target.value),
                        })
                      }
                      className="mt-2 w-full"
                    />
                  </label>

                  <label className="text-xs text-slate-600 dark:text-slate-400">
                    Confidence threshold:{' '}
                    <strong>{rule.confidence_threshold.toFixed(2)}</strong>
                    <InfoTip label="Confidence threshold">
                      Findings below this confidence receive the deterministic threshold treatment.
                    </InfoTip>
                    <input
                      type="range"
                      min={0}
                      max={1}
                      step={0.05}
                      value={rule.confidence_threshold}
                      onChange={(event) =>
                        updateRule(index, {
                          confidence_threshold: Number(event.target.value),
                        })
                      }
                      className="mt-2 w-full"
                    />
                  </label>

                  <label className="flex items-end gap-2 text-xs text-slate-600 dark:text-slate-400">
                    <input
                      type="checkbox"
                      checked={rule.escalate}
                      onChange={(event) =>
                        updateRule(index, { escalate: event.target.checked })
                      }
                    />
                    Escalate one level
                  </label>
                </div>
              </Card>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-3">
            {draft.rules.length < 12 ? (
              <Button variant="secondary" onClick={addRule}>
                Add category
              </Button>
            ) : null}

            <Button
              onClick={() => void createPolicy()}
              disabled={
                creating ||
                draft.rules.length === 0 ||
                problems.length > 0 ||
                duplicates.length > 0
              }
            >
              {creating ? 'Creating…' : 'Create Policy'}
            </Button>

            <span className="text-xs text-slate-500 dark:text-slate-400">
              Creating the policy is the first step that saves anything.
            </span>
          </div>
        </>
      )}
    </div>
  );
}

export default function NewPolicyPage() {
  return (
    <RequireAuth>
      <PolicyBuilder />
    </RequireAuth>
  );
}
