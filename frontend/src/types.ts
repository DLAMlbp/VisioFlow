export type UploadStatus = "ready" | "presigning" | "uploading" | "uploaded" | "failed";

export type UserRole = "admin" | "operator";

export interface AuthUser {
  id: string;
  username: string;
  display_name: string;
  role: UserRole;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface AuthSession {
  user: AuthUser;
  csrf_token: string;
  expires_at: string;
}

export type JobStatus =
  | "created"
  | "uploading"
  | "queued"
  | "processing"
  | "analyzing"
  | "ranking"
  | "enhancing"
  | "tagging"
  | "completed"
  | "partial_failed"
  | "failed"
  | "cancelled";

export type Decision =
  | "queued"
  | "analyzing"
  | "filtered"
  | "beautify_planning"
  | "enhancing"
  | "enhanced"
  | "selected"
  | "rejected"
  | "not_selected"
  | "failed"
  | "tagging"
  | "cancelled";

export type ResultFilter = "all" | "selected" | "rejected" | "not_selected" | "failed" | "completed" | "non_completed" | "review";

export interface UploadItem {
  id: string;
  file?: File;
  filename: string;
  fileSize: number;
  contentType: string;
  previewUrl?: string;
  status: UploadStatus;
  progress: number;
  objectKey?: string;
  error?: string;
}

export interface PresignRequest {
  filename: string;
  content_type: string;
  file_size: number;
}

export interface PresignResponse {
  object_key: string;
  upload_url: string;
}

export interface CreateJobRequest {
  filter_route?: CompletionFilterRoute;
  processing_standards?: string[];
  beautify_profile?: string;
  redaction_profile?: string;
  filter_enabled: boolean;
  beautify_enabled: boolean;
  watermark_processing_enabled: boolean;
  similarity_enabled: boolean;
  similarity_profile: string;
  unmatched_standard_policy: "reject";
  enhance_level: number;
  max_selected?: number;
  images: Array<{ object_key: string }>;
  callback_url?: string;
}

export interface CompletionFilterRoute {
  completion_profile: string;
  completed_filter_profile: string;
  non_completed_filter_profile: string;
  policy: {
    insufficient_evidence_policy: "reject" | "route_non_completed";
    low_confidence_policy: "continue_with_review" | "reject";
  };
}

export interface CreateJobResponse {
  job_id: string;
  status: JobStatus;
  total: number;
}

export interface PipelineFailure {
  node: string;
  code: string;
  message: string;
  image_id?: string | null;
  duration_ms?: number | null;
  upstream_status_code?: number | null;
  failed_at?: string | null;
}

export interface JobProgress {
  job_id: string;
  status: JobStatus;
  progress: number;
  total: number;
  processed: number;
  selected: number;
  rejected: number;
  not_selected: number;
  tagging: number;
  stage_counts: Record<string, number>;
  failure?: PipelineFailure | null;
}

export interface JobHistoryItem {
  job_id: string;
  status: JobStatus;
  total: number;
  processed: number;
  selected: number;
  rejected: number;
  not_selected: number;
  ai_tagging_model?: string | null;
  created_at: string;
  completed_at?: string | null;
  failure?: PipelineFailure | null;
}

export interface JobHistoryResponse {
  total: number;
  limit: number;
  offset: number;
  items: JobHistoryItem[];
}

export interface ImageMetrics {
  sharpness?: number;
  exposure?: number;
  contrast?: number;
  noise?: number;
}

export interface AIImageTags {
  status: "pending" | "completed" | "failed";
  source?: "library" | "legacy_ai";
  summary?: string | null;
  content_type?: string | null;
  scene?: string | null;
  space?: string | null;
  view?: string | null;
  condition?: string | null;
  subjects: string[];
  objects: string[];
  attributes: Record<string, string[]>;
  features: Record<string, string[]>;
  ocr_text: string[];
  tags: string[];
  categories: Record<string, string[]>;
  candidate_tags: string[];
  confidence?: number | null;
  content_confidence?: number | null;
  risks: string[];
  source_object_key?: string | null;
  error_message?: string | null;
}

export interface SimilarityTaggingResult {
  decision: "matched" | "unmatched";
  tags: string[];
  matched_asset_id?: string | null;
  similarity?: number | null;
  feature_score?: number | null;
  final_score?: number | null;
  auto_threshold?: number | null;
  feature_auto_threshold?: number | null;
  message: string;
}

export interface BeautifyAudit {
  status: string;
  needed?: boolean | null;
  reason?: string | null;
  confidence?: number | null;
  planned_parameters: Record<string, unknown>;
  effective_parameters: Record<string, unknown>;
  corrections: string[];
  preview_attempts: number;
  acceptance?: {
    status: "passed" | "fallback" | "failed";
    checks: Array<{
      name: "exposure" | "color" | "noise" | "sharpening";
      passed: boolean;
      before: Record<string, number>;
      after: Record<string, number>;
      reason: string;
    }>;
    fallback_reason?: string | null;
  } | null;
  redaction?: {
    standard?: Record<string, unknown>;
    screening?: {
      left_bottom_watermark_detected?: boolean;
      target_logo_detected?: boolean;
      branded_ground_film?: {
        detected?: boolean;
        brand_detected?: boolean;
        coverage_ratio?: number;
        confidence?: number;
        reason?: string;
      };
    } | null;
    watermark: Record<string, unknown>;
    logos: Record<string, unknown>;
  } | null;
}

export interface LogoRedactionUpdate {
  image_id: string;
  status: "manual_applied" | "manual_cleared";
  boxes: [number, number, number, number][];
  image_size: [number, number];
  detections: number;
  source: "manual_review";
}

export interface ClassificationContentAnalysis {
  summary: string;
  content_type: string;
  scene: string;
  spaces: string[];
  view: string;
  subjects: string[];
  objects: string[];
  visible_conditions: string[];
  attributes: Record<string, string[]>;
  supporting_evidence: string[];
  conflicting_evidence: string[];
  missing_evidence: string[];
  uncertainties: string[];
  ocr_text: string[];
  confidence: number;
}

export interface ResultImage {
  image_id: string;
  rank: number | null;
  score: number;
  decision: Decision;
  original_url?: string;
  enhanced_url?: string;
  original_download_url?: string;
  enhanced_download_url?: string;
  files_expired?: boolean;
  metrics: ImageMetrics;
  enhanced_metrics?: ImageMetrics;
  reasons: string[];
  warnings: string[];
  reject_codes?: string[];
  duplicate_group_id?: string;
  ai_tags?: AIImageTags;
  library_tags?: SimilarityTaggingResult;
  tagging_result?: SimilarityTaggingResult;
  processing_standard_id?: string | null;
  processing_standard_name?: string | null;
  activation_reason?: string | null;
  audit_dimensions?: Array<{
    dimension: string;
    passed: boolean;
    reason: string;
  }>;
  completion?: {
    label: "completed" | "non_completed";
    subtype: "completed" | "construction" | "insufficient_evidence" | "invalid_or_irrelevant";
    confidence: number;
    reason: string;
    reason_codes: string[];
    review_required: boolean;
  };
  classification?: {
    standard_id: string;
    standard_name: string;
    confidence: number;
    reason: string;
    review_required: boolean;
    content_analysis?: ClassificationContentAnalysis | null;
  };
  beautify?: BeautifyAudit;
  routed_filter_profile_id?: string | null;
  routed_filter_profile_version?: number | null;
  pipeline_stage: string;
  classification_status?: string | null;
  filter_status?: string | null;
  beautify_status?: string | null;
  analysis_status?: string | null;
  embedding_status?: string | null;
  match_status?: string | null;
}

export interface JobResults {
  job_id: string;
  summary: {
    total: number;
    selected: number;
    rejected: number;
    not_selected: number;
  };
  result_total: number;
  limit: number;
  offset: number;
  images: ResultImage[];
  failure?: PipelineFailure | null;
}

export interface UploadBatchRegistration {
  batch_id: string;
  status: string;
  expires_at: string;
  items: Array<{
    id: string;
    filename: string;
    content_type: string;
    file_size: number;
    object_key: string;
    upload_url: string;
  }>;
}

export interface ProfileOption {
  id: string;
  name: string;
  description: string;
  version?: number;
  status?: string;
  editable?: boolean;
  is_fallback?: boolean | null;
}

export type ProcessingProfileType = "filter" | "beautify" | "redaction" | "completion";

export interface ProcessingProfile extends ProfileOption {
  profile_type: ProcessingProfileType;
  instruction: string;
  config: Record<string, unknown>;
  version: number;
}

export interface ProcessingStandard extends ProfileOption {
  profile_type: "standard";
  classification_rule: string;
  filter_rule: string;
  priority: number;
  is_fallback: boolean;
  version: number;
}

export interface SaveProcessingStandard {
  name: string;
  classification_rule: string;
  filter_rule: string;
  priority: number;
  is_fallback: boolean;
  description: string;
  expected_version?: number;
}

export interface ProfilePreview {
  description: string;
  config: Record<string, unknown>;
  unsupported: string[];
  can_save: boolean;
}

export interface SaveProcessingProfile {
  name: string;
  instruction: string;
  description: string;
  config: Record<string, unknown>;
  expected_version?: number;
}

export interface AIModelConfig {
  enabled: boolean;
  api_key_configured: boolean;
}

export interface UpdateAIModelConfig {
  enabled: boolean;
  api_key?: string;
}

export interface LibraryAssetGroup {
  id: string;
  tags: string[];
  sort_order: number;
  status: "active" | "disabled";
  asset_count: number;
}

export interface LibraryAsset {
  id: string;
  original_object_key: string;
  thumbnail_object_key?: string | null;
  original_filename?: string | null;
  group_id: string;
  tags: string[];
  content_type?: string | null;
  width?: number | null;
  height?: number | null;
  status: "pending" | "active" | "failed" | "disabled";
  error_message?: string | null;
  analysis?: Record<string, unknown> | null;
  created_at: string;
  preview_url?: string;
}

export interface LibraryAssetList {
  total: number;
  items: LibraryAsset[];
}

export interface LibraryAssetBulkDeleteResult {
  deleted_count: number;
  failed_count: number;
  failed_asset_ids: string[];
}
