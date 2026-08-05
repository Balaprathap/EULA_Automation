/**
 * Typed API client.
 *
 * Every request carries the Supabase access token; the backend verifies it and
 * resolves the organization server-side. Errors arrive in the API's standard
 * envelope and are rethrown as ApiClientError so the UI can show the message
 * and the request id.
 */

import { env } from './env';
import { getSupabase } from './supabase';
import type {
  ActionItem,
  ActionItemSummary,
  ActionItemUpdate,
  Analysis,
  ApiError,
  Document,
  Evidence,
  Finding,
  Policy,
  PolicyRule,
  ReportStatus,
  Review,
} from './types';

const BASE = env.apiBaseUrl;

export class ApiClientError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly requestId?: string,
    readonly details?: Record<string, unknown>,
  ) {
    super(message);
    this.name = 'ApiClientError';
  }
}

async function authHeaders(): Promise<Record<string, string>> {
  const {
    data: { session },
  } = await getSupabase().auth.getSession();
  if (!session?.access_token) {
    throw new ApiClientError('You are not signed in.', 401, 'AUTHENTICATION_REQUIRED');
  }
  return { Authorization: `Bearer ${session.access_token}` };
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = await authHeaders();
  const isFormData = init.body instanceof FormData;

  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...headers,
      ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
      ...(init.headers ?? {}),
    },
  });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const body = text ? JSON.parse(text) : {};

  if (!response.ok) {
    const error: ApiError = body.error ?? {
      code: 'UNKNOWN',
      message: `Request failed with status ${response.status}.`,
      request_id: '',
    };
    throw new ApiClientError(
      error.message,
      response.status,
      error.code,
      error.request_id,
      error.details,
    );
  }
  return body as T;
}

export const api = {
  // --- documents ---
  listDocuments: (params: { limit?: number; offset?: number; search?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.limit) query.set('limit', String(params.limit));
    if (params.offset) query.set('offset', String(params.offset));
    if (params.search) query.set('search', params.search);
    return request<{ items: Document[]; total: number; limit: number; offset: number }>(
      `/api/v1/documents?${query.toString()}`,
    );
  },

  getDocument: (id: string) => request<Document>(`/api/v1/documents/${id}`),

  getDocumentText: (id: string) =>
    request<{ document_id: string; normalized_text: string; char_count: number }>(
      `/api/v1/documents/${id}/text`,
    ),

  uploadDocument: (file: File, meta: { vendor_name?: string; title?: string } = {}) => {
    const form = new FormData();
    form.append('file', file);
    if (meta.vendor_name) form.append('vendor_name', meta.vendor_name);
    if (meta.title) form.append('title', meta.title);
    return request<Document>('/api/v1/documents', { method: 'POST', body: form });
  },

  pasteDocument: (payload: { title: string; text: string; vendor_name?: string }) =>
    request<Document>('/api/v1/documents/paste', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  updateDocument: (id: string, payload: { title?: string; vendor_name?: string }) =>
    request<Document>(`/api/v1/documents/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  deleteDocument: (id: string) => request<void>(`/api/v1/documents/${id}`, { method: 'DELETE' }),

  // --- policies ---
  listPolicies: () => request<Policy[]>('/api/v1/policies'),
  getPolicy: (id: string) => request<Policy>(`/api/v1/policies/${id}`),
  listRules: (policyId: string) => request<PolicyRule[]>(`/api/v1/policies/${policyId}/rules`),

  createPolicy: (payload: { name: string; description?: string; rules: unknown[] }) =>
    request<Policy>('/api/v1/policies', { method: 'POST', body: JSON.stringify(payload) }),

  updatePolicy: (
    id: string,
    payload: { name?: string; description?: string; is_active?: boolean; is_default?: boolean },
  ) => request<Policy>(`/api/v1/policies/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),

  createPolicyVersion: (id: string) =>
    request<Policy>(`/api/v1/policies/${id}/versions`, { method: 'POST' }),

  replaceRules: (policyId: string, rules: unknown[]) =>
    request<PolicyRule[]>(`/api/v1/policies/${policyId}/rules`, {
      method: 'PUT',
      body: JSON.stringify({ rules }),
    }),

  // --- analyses ---
  startAnalysis: (documentId: string, policyId?: string) =>
    request<Analysis>(`/api/v1/documents/${documentId}/analyses`, {
      method: 'POST',
      body: JSON.stringify({ policy_id: policyId ?? null }),
    }),

  getAnalysis: (id: string) => request<Analysis>(`/api/v1/analyses/${id}`),

  listAnalyses: () =>
    request<{ items: Analysis[]; total: number }>('/api/v1/analyses?limit=50'),

  listFindings: (
    analysisId: string,
    filters: {
      category?: string;
      severity?: string;
      review_status?: string;
      include_quarantined?: boolean;
    } = {},
  ) => {
    const query = new URLSearchParams();
    if (filters.category) query.set('category', filters.category);
    if (filters.severity) query.set('severity', filters.severity);
    if (filters.review_status) query.set('review_status', filters.review_status);
    if (filters.include_quarantined) query.set('include_quarantined', 'true');
    return request<Finding[]>(`/api/v1/analyses/${analysisId}/findings?${query.toString()}`);
  },

  // --- findings ---
  getEvidence: (findingId: string) =>
    request<Evidence>(`/api/v1/findings/${findingId}/evidence`),

  reviewFinding: (
    findingId: string,
    payload: { action: string; severity?: string; note?: string; reason?: string },
  ) =>
    request<Review>(`/api/v1/findings/${findingId}/reviews`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  listReviews: (findingId: string) =>
    request<Review[]>(`/api/v1/findings/${findingId}/reviews`),

  // --- action items ---
  listActionItems: (
    filters: {
      status?: string;
      category?: string;
      priority?: string;
      document_id?: string;
      analysis_id?: string;
      due?: string;
      sort?: string;
      limit?: number;
    } = {},
  ) => {
    const query = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== '') query.set(key, String(value));
    });
    return request<{ items: ActionItem[]; total: number; limit: number; offset: number }>(
      `/api/v1/action-items?${query.toString()}`,
    );
  },

  getActionItemSummary: () => request<ActionItemSummary>('/api/v1/action-items/summary'),

  updateActionItem: (itemId: string, payload: ActionItemUpdate) =>
    request<ActionItem>(`/api/v1/action-items/${itemId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  generateActionItems: (analysisId: string) =>
    request<{ analysis_id: string; derived: number; created: number }>(
      `/api/v1/analyses/${analysisId}/action-items/generate`,
      { method: 'POST' },
    ),

  // --- reports ---
  getReportStatus: (analysisId: string) =>
    request<ReportStatus>(`/api/v1/analyses/${analysisId}/report/status`),

  /**
   * Resend the report. Deliberately sends no body: the recipient is resolved
   * server-side from the authenticated session and cannot be chosen here.
   */
  resendReportEmail: (analysisId: string) =>
    request<ReportStatus>(`/api/v1/analyses/${analysisId}/report/email`, { method: 'POST' }),

  /** Download the PDF as a Blob, using the authenticated API route. */
  downloadReport: async (analysisId: string): Promise<{ blob: Blob; filename: string }> => {
    const headers = await authHeaders();
    const response = await fetch(`${BASE}/api/v1/analyses/${analysisId}/report`, { headers });
    if (!response.ok) {
      const text = await response.text();
      const body = text ? JSON.parse(text) : {};
      const error: ApiError = body.error ?? {
        code: 'UNKNOWN',
        message: `Download failed with status ${response.status}.`,
        request_id: '',
      };
      throw new ApiClientError(error.message, response.status, error.code, error.request_id);
    }
    const disposition = response.headers.get('content-disposition') ?? '';
    const match = /filename="?([^";]+)"?/.exec(disposition);
    return { blob: await response.blob(), filename: match?.[1] ?? `clauseguard-report.pdf` };
  },

  // --- usage / admin ---
  getDashboard: () => request<Record<string, any>>('/api/v1/dashboard'),
  getUsage: (days = 30) => request<Record<string, any>>(`/api/v1/usage?days=${days}`),
  getAdminMetrics: () => request<Record<string, any>>('/api/v1/admin/metrics'),
};
