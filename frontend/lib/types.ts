export type Severity = 'info' | 'low' | 'medium' | 'high' | 'critical';
export type RiskBand = 'low' | 'moderate' | 'elevated' | 'high';
export type AnalysisStage =
  | 'queued'
  | 'parsing'
  | 'chunking'
  | 'retrieving'
  | 'extracting'
  | 'verifying'
  | 'scoring'
  | 'complete'
  | 'failed';

export const STAGES: { key: AnalysisStage; label: string }[] = [
  { key: 'parsing', label: 'Parsing' },
  { key: 'chunking', label: 'Chunking' },
  { key: 'retrieving', label: 'Retrieving' },
  { key: 'extracting', label: 'Extracting' },
  { key: 'verifying', label: 'Verifying' },
  { key: 'scoring', label: 'Scoring' },
  { key: 'complete', label: 'Complete' },
];

export interface ApiError {
  code: string;
  message: string;
  request_id: string;
  details?: Record<string, unknown>;
}

export interface Document {
  id: string;
  title: string;
  vendor_name: string | null;
  source_type: 'pdf' | 'docx' | 'txt' | 'paste';
  original_filename: string | null;
  file_size_bytes: number | null;
  page_count: number | null;
  char_count: number | null;
  status: 'uploaded' | 'parsing' | 'chunking' | 'ready' | 'failed';
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface CategoryProgress {
  category: string;
  status: 'pending' | 'completed' | 'abstained' | 'needs_review' | 'failed';
  needs_review_reason: string | null;
  error_code: string | null;
  retrieval_mode: string | null;
  degraded_reason: string | null;
  tool_calls: number;
  duration_ms: number | null;
}

export interface Analysis {
  id: string;
  document_id: string;
  policy_id: string;
  status: 'queued' | 'running' | 'complete' | 'partial' | 'failed' | 'cancelled';
  stage: AnalysisStage;
  progress_message: string | null;
  categories_total: number;
  categories_completed: number;
  completed_categories: string[];
  overall_score: number | null;
  risk_band: RiskBand | null;
  finding_count: number;
  review_count: number;
  quarantine_count: number;
  verification_pass_rate: number | null;
  executive_summary: string | null;
  degraded_retrieval: boolean;
  degraded_reason: string | null;
  stage_timings_ms: Record<string, number>;
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  estimated_cost_usd: number;
  model_used: string | null;
  error_code: string | null;
  error_message: string | null;
  categories: CategoryProgress[];
  created_at: string;
  completed_at: string | null;
}

export interface Finding {
  id: string;
  analysis_id: string;
  document_id: string;
  category: string;
  plain_summary: string;
  why_it_matters: string;
  model_confidence: number;
  severity_weight: number;
  confidence_threshold: number;
  weighted_risk: number;
  machine_severity: Severity;
  override_severity: Severity | null;
  effective_severity: Severity;
  severity_source: 'deterministic' | 'human_override' | 'degraded_cap';
  scoring_explanation: string;
  review_status: 'pending' | 'accepted' | 'dismissed' | 'escalated';
  verification_status: 'pending' | 'verified' | 'quarantined' | 'needs_review';
  quarantine_reason: string | null;
  degraded_retrieval: boolean;
  quote: string | null;
  doc_start_offset: number | null;
  doc_end_offset: number | null;
  verification_method: string | null;
  chunk_ordinal: number | null;
  chunk_heading: string | null;
  created_at: string;
}

export interface Evidence {
  finding_id: string;
  quote: string;
  doc_start_offset: number;
  doc_end_offset: number;
  verification_method: string;
  verified_at: string;
  chunk_text: string | null;
  chunk_heading: string | null;
  surrounding_text: string | null;
  surrounding_start_offset: number | null;
}

export interface PolicyRule {
  id: string;
  policy_id: string;
  category: string;
  display_name: string;
  description: string;
  retrieval_guidance: string | null;
  keywords: string[];
  severity_weight: number;
  confidence_threshold: number;
  escalate: boolean;
  is_enabled: boolean;
  sort_order: number;
}

export interface Policy {
  id: string;
  name: string;
  description: string | null;
  version: number;
  is_default: boolean;
  is_active: boolean;
  rule_count: number;
  created_at: string;
  updated_at: string;
}

export interface Review {
  id: string;
  action: string;
  previous_severity: string | null;
  new_severity: string | null;
  new_status: string | null;
  note: string | null;
  reason: string | null;
  reviewer_name: string | null;
  reviewer_email: string | null;
  created_at: string;
}

export type ReportGenerationStatus = 'pending' | 'generating' | 'ready' | 'failed';
export type ReportEmailStatus = 'pending' | 'sending' | 'sent' | 'failed' | 'permanently_failed';

export interface ReportStatus {
  analysis_id: string;
  analysis_status: string;
  report_available: boolean;
  generation_status: ReportGenerationStatus;
  version: number | null;
  file_size: number | null;
  generated_at: string | null;
  email_status: ReportEmailStatus | null;
  email_attempts: number;
  /** Masked for display, e.g. a***@example.com. Never the full address. */
  email_masked_recipient: string | null;
  email_sent_at: string | null;
  email_error: string | null;
  can_resend: boolean;
}

export type ActionItemStatus = 'open' | 'in_progress' | 'completed' | 'dismissed';
export type ActionItemPriority = 'low' | 'medium' | 'high' | 'urgent';
export type DateStatus = 'unresolved' | 'ai_extracted' | 'human_set' | 'not_applicable';

export interface ActionItem {
  id: string;
  analysis_id: string;
  document_id: string;
  finding_id: string;
  document_title: string | null;
  vendor_name: string | null;
  title: string;
  description: string;
  category: string;
  obligation_type: string;
  /** Verbatim text already verified against the source document. */
  evidence_quote: string;
  doc_start_offset: number | null;
  doc_end_offset: number | null;
  duration_days: number | null;
  duration_text: string | null;
  ai_priority: ActionItemPriority;
  due_date: string | null;
  date_status: DateStatus;
  assignee_id: string | null;
  priority: ActionItemPriority;
  status: ActionItemStatus;
  reviewer_note: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ActionItemSummary {
  open_count: number;
  completed_count: number;
  overdue_count: number;
  due_soon_count: number;
  urgent_count: number;
  unresolved_date_count: number;
}

export interface ActionItemUpdate {
  due_date?: string | null;
  assignee_id?: string | null;
  priority?: ActionItemPriority;
  status?: ActionItemStatus;
  reviewer_note?: string | null;
}
