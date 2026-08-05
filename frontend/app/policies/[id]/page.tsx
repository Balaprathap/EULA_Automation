'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

import { RequireAuth } from '@/components/RequireAuth';
import { useToast } from '@/components/Toast';
import {
  Breadcrumbs,
  Button,
  Card,
  ConfirmDialog,
  ErrorState,
  InfoTip,
  SectionHeading,
  Spinner,
  Warning,
  inputClasses,
} from '@/components/ui';
import { api } from '@/lib/api';
import type { Policy, PolicyRule } from '@/lib/types';

type EditableRule = Omit<PolicyRule, 'id' | 'policy_id'> & { id?: string };

const BLANK: EditableRule = {
  category: '',
  display_name: '',
  description: '',
  retrieval_guidance: '',
  keywords: [],
  severity_weight: 0.5,
  confidence_threshold: 0.35,
  escalate: false,
  is_enabled: true,
  sort_order: 999,
};

function PolicyEditor() {
  const { id } = useParams<{ id: string }>();
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [rules, setRules] = useState<EditableRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [confirmingSave, setConfirmingSave] = useState(false);
  const toast = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [policyData, ruleData] = await Promise.all([api.getPolicy(id), api.listRules(id)]);
      setPolicy(policyData);
      setRules(ruleData);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load the policy.');
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  function update(index: number, patch: Partial<EditableRule>) {
    setRules((current) => current.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)));
    setSaved(false);
  }

  const problems = rules.flatMap((rule, index) => {
    const issues: string[] = [];
    if (!/^[a-z][a-z0-9_]*$/.test(rule.category)) {
      issues.push(`Row ${index + 1}: category must be lowercase with underscores.`);
    }
    if (!rule.display_name.trim()) issues.push(`Row ${index + 1}: a display name is required.`);
    if (!rule.description.trim()) issues.push(`Row ${index + 1}: a description is required.`);
    return issues;
  });

  const duplicates = rules
    .map((r) => r.category)
    .filter((category, index, all) => all.indexOf(category) !== index);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const updated = await api.replaceRules(
        id,
        rules.map((rule, index) => ({ ...rule, sort_order: index })),
      );
      setRules(updated);
      setSaved(true);
      toast.success('Policy categories saved. Future analyses will use these values.');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'The policy could not be saved.';
      setError(message);
      toast.error(message);
    } finally {
      setSaving(false);
    }
  }

  async function newVersion() {
    setSaving(true);
    try {
      const created = await api.createPolicyVersion(id);
      window.location.href = `/policies/${created.id}`;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'A new version could not be created.');
      setSaving(false);
    }
  }

  if (loading) return <Spinner label="Loading policy" />;
  if (!policy) return <ErrorState error={error ?? 'Policy not found.'} onRetry={load} />;

  return (
    <div className="space-y-6">
      <Breadcrumbs
        items={[
          { label: 'Dashboard', href: '/dashboard' },
          { label: 'Policies', href: '/policies' },
          { label: policy.name },
        ]}
      />

      <SectionHeading
        title={`${policy.name} (v${policy.version})`}
        description="Severity weight and threshold determine how a confirmed clause is scored. They stay in your database and are never included in any prompt."
      />

      <Warning title="These values change how risk is scored">
        Editing a weight or threshold changes the severity of every <em>future</em> analysis run
        against this policy. Completed analyses keep the rules they actually ran with. If you want
        to preserve the current behaviour, use <strong>Save as a new version</strong> instead.
      </Warning>

      {error ? <ErrorState error={error} /> : null}
      {problems.length > 0 ? (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          <ul className="list-inside list-disc">
            {problems.slice(0, 6).map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {duplicates.length > 0 ? (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          Duplicate categories: {Array.from(new Set(duplicates)).join(', ')}
        </div>
      ) : null}

      <div className="space-y-3">
        {rules.map((rule, index) => (
          <Card key={rule.id ?? `new-${index}`} className="space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <input
                value={rule.display_name}
                onChange={(e) => update(index, { display_name: e.target.value })}
                placeholder="Display name"
                className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm font-medium"
              />
              <input
                value={rule.category}
                onChange={(e) => update(index, { category: e.target.value })}
                placeholder="category_key"
                className="w-56 rounded-md border border-slate-300 px-3 py-2 font-mono text-xs"
              />
              <label className="flex items-center gap-2 text-xs text-slate-600">
                <input
                  type="checkbox"
                  checked={rule.is_enabled}
                  onChange={(e) => update(index, { is_enabled: e.target.checked })}
                />
                Enabled
              </label>
              <button
                onClick={() => {
                  setRules((current) => current.filter((_, i) => i !== index));
                  setSaved(false);
                }}
                className="text-xs text-red-600 hover:underline"
              >
                Remove
              </button>
            </div>

            <textarea
              rows={2}
              value={rule.description}
              onChange={(e) => update(index, { description: e.target.value })}
              placeholder="What this category covers - shown to the model."
              className={`${inputClasses} mt-0 text-sm`}
            />

            <textarea
              rows={2}
              value={rule.retrieval_guidance ?? ''}
              onChange={(e) => update(index, { retrieval_guidance: e.target.value })}
              placeholder="Retrieval guidance - phrasing that helps find the right clauses."
              className={`${inputClasses} mt-0 text-sm`}
            />

            <input
              value={rule.keywords.join(', ')}
              onChange={(e) =>
                update(index, {
                  keywords: e.target.value
                    .split(',')
                    .map((k) => k.trim())
                    .filter(Boolean),
                })
              }
              placeholder="Keywords, comma separated"
              className={`${inputClasses} mt-0 text-sm`}
            />

            <div className="grid gap-4 sm:grid-cols-3">
              <label className="text-xs text-slate-600 dark:text-slate-400">
                Severity weight: <strong>{rule.severity_weight.toFixed(2)}</strong>
                <InfoTip label="Severity weight">
                  How serious a confirmed clause in this category is. Multiplied by the model&apos;s
                  confidence to produce the severity. Never sent to the model.
                </InfoTip>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={rule.severity_weight}
                  onChange={(e) => update(index, { severity_weight: Number(e.target.value) })}
                  className="mt-1 w-full"
                />
              </label>
              <label className="text-xs text-slate-600 dark:text-slate-400">
                Confidence threshold: <strong>{rule.confidence_threshold.toFixed(2)}</strong>
                <InfoTip label="Confidence threshold">
                  Minimum model confidence before a finding counts as confirmed. Below it, the
                  severity is demoted one level.
                </InfoTip>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={rule.confidence_threshold}
                  onChange={(e) => update(index, { confidence_threshold: Number(e.target.value) })}
                  className="mt-1 w-full"
                />
              </label>
              <label className="flex items-end gap-2 text-xs text-slate-600 dark:text-slate-400">
                <input
                  type="checkbox"
                  checked={rule.escalate}
                  onChange={(e) => update(index, { escalate: e.target.checked })}
                />
                Escalate one level
              </label>
            </div>
          </Card>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="secondary"
          onClick={() => {
            setRules((current) => [...current, { ...BLANK }]);
            setSaved(false);
          }}
        >
          Add a category
        </Button>
        <Button
          onClick={() => setConfirmingSave(true)}
          disabled={saving || problems.length > 0 || duplicates.length > 0 || rules.length === 0}
        >
          {saving ? 'Saving…' : 'Save categories'}
        </Button>
        <Button variant="ghost" onClick={newVersion} disabled={saving}>
          Save as a new version
        </Button>
        {saved ? (
          <span className="text-sm text-emerald-700 dark:text-emerald-400">Saved.</span>
        ) : null}
      </div>

      <ConfirmDialog
        open={confirmingSave}
        title="Change how risk is scored?"
        confirmLabel="Save categories"
        body={
          <>
            <p>
              This updates the scoring rules for <strong>{policy.name} v{policy.version}</strong>.
              Every future analysis using this policy will be scored with the new weights and
              thresholds.
            </p>
            <p className="mt-2">
              Analyses that have already completed are unaffected — they keep the rules they ran
              with.
            </p>
          </>
        }
        onConfirm={() => {
          setConfirmingSave(false);
          void save();
        }}
        onCancel={() => setConfirmingSave(false)}
      />

      <p className="text-xs text-slate-500 dark:text-slate-400">
        Creating a new version leaves completed analyses pointing at the rules they actually ran
        with, so historical scores stay reproducible.
      </p>
    </div>
  );
}

export default function PolicyEditorPage() {
  return (
    <RequireAuth>
      <PolicyEditor />
    </RequireAuth>
  );
}
