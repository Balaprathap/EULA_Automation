import Link from 'next/link';

const FEATURES = [
  {
    title: 'Every finding is backed by a verified quote',
    body: 'Each quote is checked back against the stored document before it is shown. Anything that cannot be located is quarantined and excluded from the score, so a fabricated citation never reaches you.',
  },
  {
    title: 'Severity is computed, not guessed',
    body: 'The model reports confidence and evidence. Your policy supplies the weights and thresholds, and the application multiplies them in ordinary code, so a contract cannot talk its way into a lower risk rating.',
  },
  {
    title: 'Hybrid retrieval over the whole agreement',
    body: 'Vector similarity finds clauses that mean the right thing; full-text search catches the exact terms of art. Reciprocal Rank Fusion combines both, and every fallback is reported rather than hidden.',
  },
  {
    title: 'Reviewer workflow with full history',
    body: 'Accept, dismiss, escalate, or override any finding. The original machine decision is preserved alongside yours, so the audit trail stays intact.',
  },
];

export default function LandingPage() {
  return (
    <div className="space-y-16">
      <section className="mx-auto max-w-3xl py-12 text-center">
        <p className="text-sm font-semibold uppercase tracking-widest text-slate-500 dark:text-slate-400">
          Automated EULA Compliance Extraction
        </p>
        <h1 className="mt-3 text-4xl font-bold tracking-tight text-slate-900 sm:text-5xl dark:text-slate-100">
          Find the clauses that matter, with proof
        </h1>
        <p className="mx-auto mt-5 max-w-2xl text-lg text-slate-600 dark:text-slate-400">
          ClauseGuard reads end user licence agreements, terms of service, SaaS contracts, and
          vendor agreements, and surfaces the compliance-relevant clauses inside them - each one
          traced back to the exact text it came from.
        </p>
        <div className="mt-8 flex justify-center gap-3">
          <Link
            href="/register"
            className="rounded-md bg-slate-900 px-5 py-3 text-sm font-medium text-white hover:bg-slate-700 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white"
          >
            Get started
          </Link>
          <Link
            href="/login"
            className="rounded-md border border-slate-300 bg-white px-5 py-3 text-sm font-medium hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800"
          >
            Sign in
          </Link>
        </div>
      </section>

      <section className="grid gap-6 md:grid-cols-2">
        {FEATURES.map((feature) => (
          <div key={feature.title} className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
            <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">{feature.title}</h2>
            <p className="mt-2 text-sm leading-relaxed text-slate-600 dark:text-slate-400">{feature.body}</p>
          </div>
        ))}
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 dark:border-slate-800 dark:bg-slate-900">
        <h2 className="text-base font-semibold text-slate-900 dark:text-slate-100">How an analysis runs</h2>
        <ol className="mt-4 grid gap-3 text-sm text-slate-600 md:grid-cols-3 dark:text-slate-400">
          {[
            ['1. Upload or paste', 'PDF, DOCX, or TXT. Scanned PDFs are rejected with an explanation rather than analyzed as empty.'],
            ['2. Parse and chunk', 'Text is normalized once, then split on real clause boundaries with exact character offsets retained.'],
            ['3. Retrieve', 'For each policy category, hybrid search selects only the most relevant clauses - never the whole contract.'],
            ['4. Extract', 'Claude proposes findings with verbatim quotes, bounded by a strict schema and a small read-only toolset.'],
            ['5. Verify', 'Every quote is located in the source. Unverifiable evidence is quarantined, not displayed.'],
            ['6. Score', 'Your policy weights produce the severity and the overall risk band, deterministically.'],
          ].map(([title, body]) => (
            <li key={title}>
              <p className="font-medium text-slate-900 dark:text-slate-100">{title}</p>
              <p className="mt-1">{body}</p>
            </li>
          ))}
        </ol>
      </section>

      <p className="rounded-md border border-slate-200 bg-slate-100 px-4 py-3 text-sm text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-400">
        <strong>Not legal advice.</strong> ClauseGuard is an aid to human review, not a substitute
        for a qualified lawyer.
      </p>
    </div>
  );
}
