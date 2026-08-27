export type UploadStatus = "ready" | "presigning" | "uploading" | "uploaded" | "failed";

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
  | "enhancing"
  | "enhanced"
  | "selected"
  | "rejected"
  | "not_selected"
  | "failed"
  | "tagging"
  | "cancelled";

export type ResultFilter = "all" | "selected" | "rejected" | "not_selected" | "failed";

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
  filter_profile: string;
  beautify_profile: string;
  similarity_profile: string;
  enhance_level: number;
  max_selected: number;
  images: Array<{ object_key: string }>;
  callback_url?: string;
}

export interface CreateJobResponse {
  job_id: string;
  status: JobStatus;
  total: number;
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
  summary?: string | null;
  tags: string[];
  categories: Record<string, string[]>;
  candidate_tags: string[];
  confidence?: number | null;
  risks: string[];
  source_object_key?: string | null;
  error_message?: string | null;
}

export interface SimilarityTaggingResult {
  decision: "matched" | "pending_review" | "unmatched";
  tag_path: string[];
  matched_asset_id?: string | null;
  similarity?: number | null;
  final_score?: number | null;
  message: string;
}

export interface SimilarityCandidate {
  asset_id: string;
  tag_path: string[];
  similarity_score: number;
  feature_score: number;
  final_score: number;
}

export interface TagReview {
  image_id: string;
  matched_asset_id?: string | null;
  tag_path: string[];
  similarity_score?: number | null;
  feature_score?: number | null;
  final_score?: number | null;
  decision: SimilarityTaggingResult["decision"];
  message: string;
  candidates: SimilarityCandidate[];
}

export interface ResultImage {
  image_id: string;
  rank: number | null;
  score: number;
  decision: Decision;
  original_url?: string;
  enhanced_url?: string;
  files_expired?: boolean;
  metrics: ImageMetrics;
  enhanced_metrics?: ImageMetrics;
  reasons: string[];
  warnings: string[];
  reject_codes?: string[];
  duplicate_group_id?: string;
  ai_tags?: AIImageTags;
  tagging_result?: SimilarityTaggingResult;
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
}

export interface AIModelConfig {
  enabled: boolean;
  api_key_configured: boolean;
}

export interface UpdateAIModelConfig {
  enabled: boolean;
  api_key?: string;
}

export interface LibraryTagNode {
  id: string;
  parent_id?: string | null;
  name: string;
  depth: number;
  sort_order: number;
  status: "active" | "disabled";
  asset_count: number;
  children: LibraryTagNode[];
}

export interface LibraryAsset {
  id: string;
  original_object_key: string;
  original_filename?: string | null;
  leaf_tag_node_id: string;
  tag_path: string[];
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
