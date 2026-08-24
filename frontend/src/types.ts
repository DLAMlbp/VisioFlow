export type UploadStatus = "ready" | "presigning" | "uploading" | "uploaded" | "failed";

export type JobStatus =
  | "created"
  | "uploading"
  | "queued"
  | "analyzing"
  | "ranking"
  | "enhancing"
  | "tagging"
  | "completed"
  | "partial_failed"
  | "failed"
  | "cancelled";

export type Decision = "selected" | "rejected" | "failed" | "tagging";

export interface UploadItem {
  id: string;
  file: File;
  previewUrl: string;
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
}

export interface JobHistoryItem {
  job_id: string;
  status: JobStatus;
  total: number;
  processed: number;
  selected: number;
  rejected: number;
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

export interface ResultImage {
  image_id: string;
  rank: number | null;
  score: number;
  decision: Decision;
  original_url?: string;
  enhanced_url?: string;
  metrics: ImageMetrics;
  enhanced_metrics?: ImageMetrics;
  reasons: string[];
  warnings: string[];
  reject_codes?: string[];
  duplicate_group_id?: string;
  ai_tags?: AIImageTags;
}

export interface JobResults {
  job_id: string;
  summary: {
    total: number;
    selected: number;
    rejected: number;
  };
  images: ResultImage[];
}

export interface ProfileOption {
  id: string;
  name: string;
  description: string;
}

export interface AIModelConfig {
  enabled: boolean;
  provider: string;
  base_url: string;
  model: string;
  api_key_configured: boolean;
}

export interface UpdateAIModelConfig {
  enabled: boolean;
  base_url: string;
  model: string;
  api_key?: string;
}
