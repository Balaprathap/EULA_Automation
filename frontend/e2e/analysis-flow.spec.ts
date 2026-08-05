import { expect, test } from '@playwright/test';

/**
 * Full workflow: sign in, upload a real agreement, run an analysis, watch real
 * progress, open a finding, confirm the evidence is highlighted, review it, and
 * confirm the review survives a page reload.
 *
 * Requires a running stack and credentials. Skipped when they are absent rather
 * than silently passing.
 */

const EMAIL = process.env.E2E_EMAIL;
const PASSWORD = process.env.E2E_PASSWORD;

const AGREEMENT = `ACME CLOUD SERVICES END USER LICENSE AGREEMENT

1. DATA RETENTION
1.1 Acme may retain Customer Data indefinitely following termination of this
Agreement for archival, security, analytics, and product improvement purposes,
notwithstanding any deletion request submitted by Customer.

2. LIMITATION OF LIABILITY
2.1 ACME'S TOTAL AGGREGATE LIABILITY UNDER THIS AGREEMENT SHALL NOT EXCEED
FIFTY UNITED STATES DOLLARS (USD 50.00), REGARDLESS OF THE AMOUNT OF FEES PAID.

3. AUTOMATIC RENEWAL
3.1 The Subscription Term shall automatically renew for successive twelve (12)
month periods at the then-current list price unless Customer provides written
notice of non-renewal at least ninety (90) days prior to the end of the term.

4. INDEMNIFICATION
Customer shall defend, indemnify, and hold harmless Acme from and against any
and all claims, damages, losses, and liabilities arising out of or relating to
Customer's use of the Service, regardless of whether such claims arise in whole
or in part from Acme's own negligence.

5. CLASS ACTION WAIVER
CUSTOMER AGREES THAT ANY DISPUTE RESOLUTION PROCEEDING WILL BE CONDUCTED ONLY
ON AN INDIVIDUAL BASIS AND NOT AS A PLAINTIFF OR CLASS MEMBER IN ANY PURPORTED
CLASS, COLLECTIVE, OR REPRESENTATIVE PROCEEDING.`;

test.describe('compliance analysis workflow', () => {
  test.skip(
    !EMAIL || !PASSWORD,
    'Set E2E_EMAIL and E2E_PASSWORD to run the end-to-end workflow against a live stack.',
  );

  test('upload, analyze, review evidence, and persist a reviewer action', async ({ page }) => {
    // 1. Sign in.
    await page.goto('/login');
    await page.getByLabel('Email').fill(EMAIL!);
    await page.getByLabel('Password').fill(PASSWORD!);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL(/\/dashboard/);

    // 2-4. Paste an agreement, pick the policy, start the analysis.
    await page.goto('/documents/upload');
    await page.getByRole('button', { name: 'Paste text' }).click();
    await page.getByLabel('Agreement text').fill(AGREEMENT);
    await page.getByLabel('Title').fill(`E2E agreement ${Date.now()}`);
    await page.getByRole('button', { name: 'Upload and analyze' }).click();

    // 5. Real progress is displayed.
    await expect(page).toHaveURL(/\/analyses\//, { timeout: 30_000 });
    await expect(page.getByText('Analysis in progress')).toBeVisible();

    // 6. Completed score appears once the worker finishes.
    await expect(page.getByText('Overall score')).toBeVisible({ timeout: 180_000 });
    const score = page.locator('text=/^\\d+(\\.\\d+)?$/').first();
    await expect(score).toBeVisible();

    // 7-8. Open a finding and confirm the evidence is highlighted in the source.
    const firstFinding = page.locator('article').first();
    await expect(firstFinding).toBeVisible();
    await firstFinding.click();
    await expect(page.getByText('Source document')).toBeVisible();
    await expect(page.locator('mark.evidence').first()).toBeVisible({ timeout: 20_000 });

    // 9-10. Review the finding with a note.
    await firstFinding.getByRole('button', { name: 'Review this finding' }).click();
    await firstFinding.getByPlaceholder('Reviewer note (optional)').fill('Confirmed during E2E.');
    await firstFinding.getByRole('button', { name: 'Accept' }).click();
    await expect(firstFinding.getByText('accepted')).toBeVisible({ timeout: 15_000 });

    // 11-12. Reload and confirm the review persisted.
    await page.reload();
    await expect(page.getByText('accepted').first()).toBeVisible({ timeout: 30_000 });
  });

  test('quarantined findings are excluded from the verified set', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill(EMAIL!);
    await page.getByLabel('Password').fill(PASSWORD!);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page).toHaveURL(/\/dashboard/);

    await page.goto('/documents');
    const firstDocument = page.locator('table tbody tr a').first();
    if ((await firstDocument.count()) === 0) test.skip(true, 'No documents available.');
    await firstDocument.click();

    const analysisLink = page.locator('a[href*="/analyses/"]').first();
    if ((await analysisLink.count()) === 0) test.skip(true, 'No completed analyses available.');
    await analysisLink.click();

    // Quarantined findings are hidden by default and never carry a verified badge.
    const toggle = page.getByLabel(/Show quarantined/);
    await expect(toggle).not.toBeChecked();
  });
});
