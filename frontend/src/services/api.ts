import type {
  AIModelConfig,
  CreateJobRequest,
  CreateJobResponse,
  JobHistoryResponse,
  JobProgress,
  JobResults,
  PresignRequest,
  PresignResponse,
  ProfileOption,
  UpdateAIModelConfig
} from "../types";
import { mockApi } from "./mockApi";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";
const USE_MOCK_API = import.meta.env.VITE_USE_MOCK_API !== "false";

interface BackendResultImage {
  image_id: string;
  decision: "selected" | "rejected" | "failed";
  score: number | null;
  original_object_key: string;
  enhanced_object_key: string | null;
  reject_codes: string[];
  reasons: string[];
  metrics: JobResults["images"][number]["metrics"] | null;
  enhanced_metrics: JobResults["images"][number]["metrics"] | null;
  ai_tags: JobResults["images"][number]["ai_tags"] | null;
}

interface BackendJobResults {
  job_id: string;
  total: number;
  selected: number;
  rejected: number;
  images: BackendResultImage[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers
  });

  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `请求失败：${response.status}`);
  }

  return response.json() as Promise<T>;
}

export const api = USE_MOCK_API
  ? mockApi
  : {
      presignUpload(payload: PresignRequest): Promise<PresignResponse> {
        return request<PresignResponse>("/api/v1/uploads/presign", {
          method: "POST",
          body: JSON.stringify(payload)
        });
      },
      async uploadToStorage(uploadUrl: string, file: File, onProgress: (progress: number) => void): Promise<void> {
        await uploadWithProgress(uploadUrl, file, onProgress);
      },
      createJob(payload: CreateJobRequest): Promise<CreateJobResponse> {
        return request<CreateJobResponse>("/api/v1/image/jobs", {
          method: "POST",
          body: JSON.stringify(payload)
        });
      },
      getJob(jobId: string): Promise<JobProgress> {
        return request<JobProgress>(`/api/v1/image/jobs/${jobId}`);
      },
      getHistory(): Promise<JobHistoryResponse> {
        return request<JobHistoryResponse>("/api/v1/image/jobs?limit=30");
      },
      async getResults(jobId: string): Promise<JobResults> {
        const result = await request<BackendJobResults>(`/api/v1/image/jobs/${jobId}/results`);
        const images = await Promise.all(
          result.images.map(async (image) => ({
            image_id: image.image_id,
            rank: null,
            score: image.score ?? 0,
            decision: image.decision,
            original_url: await getDownloadUrlSafely(image.original_object_key),
            enhanced_url: image.enhanced_object_key
              ? await getDownloadUrlSafely(image.enhanced_object_key)
              : undefined,
            metrics: image.metrics ?? {},
            enhanced_metrics: image.enhanced_metrics ?? undefined,
            reasons: image.reasons,
            warnings: [],
            reject_codes: image.reject_codes,
            ai_tags: image.ai_tags ?? undefined
          }))
        );
        return {
          job_id: result.job_id,
          summary: {
            total: result.total,
            selected: result.selected,
            rejected: result.rejected
          },
          images
        };
      },
      getFilterProfiles(): Promise<ProfileOption[]> {
        return request<ProfileOption[]>("/api/v1/filter-profiles");
      },
      getBeautifyProfiles(): Promise<ProfileOption[]> {
        return request<ProfileOption[]>("/api/v1/beautify-profiles");
      },
      getAIModelConfig(): Promise<AIModelConfig> {
        return request<AIModelConfig>("/api/v1/settings/ai-model");
      },
      updateAIModelConfig(payload: UpdateAIModelConfig): Promise<AIModelConfig> {
        return request<AIModelConfig>("/api/v1/settings/ai-model", {
          method: "PUT",
          body: JSON.stringify(payload)
        });
      }
    };

async function getDownloadUrl(objectKey: string): Promise<string> {
  const response = await request<{ download_url: string }>("/api/v1/uploads/presign-download", {
    method: "POST",
    body: JSON.stringify({ object_key: objectKey })
  });
  return response.download_url;
}

async function getDownloadUrlSafely(objectKey: string): Promise<string | undefined> {
  try {
    return await getDownloadUrl(objectKey);
  } catch {
    return undefined;
  }
}

function uploadWithProgress(uploadUrl: string, file: File, onProgress: (progress: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();

    request.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };

    request.onload = () => {
      if (request.status >= 200 && request.status < 300) {
        onProgress(100);
        resolve();
      } else {
        reject(new Error(`上传失败：${request.status}`));
      }
    };

    request.onerror = () => reject(new Error("上传网络异常"));
    request.open("PUT", uploadUrl);
    request.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    request.send(file);
  });
}
