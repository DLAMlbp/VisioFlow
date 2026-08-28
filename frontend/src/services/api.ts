import type {
  AIModelConfig,
  Decision,
  CreateJobRequest,
  CreateJobResponse,
  JobHistoryResponse,
  JobProgress,
  JobResults,
  PresignRequest,
  PresignResponse,
  ProfileOption,
  ProcessingProfile,
  ProcessingProfileType,
  ProfilePreview,
  SaveProcessingProfile,
  LibraryAsset,
  LibraryAssetList,
  LibraryTagNode,
  TagReview,
  UpdateAIModelConfig,
  UploadBatchRegistration
} from "../types";
import { mockApi } from "./mockApi";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";
const USE_MOCK_API = import.meta.env.VITE_USE_MOCK_API !== "false";

interface BackendResultImage {
  image_id: string;
  decision: Decision;
  score: number | null;
  original_object_key: string;
  enhanced_object_key: string | null;
  files_expired: boolean;
  reject_codes: string[];
  reasons: string[];
  metrics: JobResults["images"][number]["metrics"] | null;
  enhanced_metrics: JobResults["images"][number]["metrics"] | null;
  ai_tags: JobResults["images"][number]["ai_tags"] | null;
  tagging_result: JobResults["images"][number]["tagging_result"] | null;
}

interface BackendJobResults {
  job_id: string;
  total: number;
  selected: number;
  rejected: number;
  not_selected: number;
  result_total: number;
  limit: number;
  offset: number;
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

  if (response.status === 204) return undefined as T;
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
      getObjectPreviewUrl(objectKey: string): Promise<string | undefined> {
        return getDownloadUrlSafely(objectKey);
      },
      createJob(payload: CreateJobRequest): Promise<CreateJobResponse> {
        return request<CreateJobResponse>("/api/v1/image/jobs", {
          method: "POST",
          body: JSON.stringify(payload)
        });
      },
      createUploadBatch(payload: {
        filter_profile: string;
        beautify_profile: string;
        similarity_profile: string;
        enhance_level: number;
        max_selected: number;
        files: PresignRequest[];
      }): Promise<UploadBatchRegistration> {
        return request<UploadBatchRegistration>("/api/v1/upload-batches", {
          method: "POST",
          body: JSON.stringify(payload)
        });
      },
      completeUploadBatch(batchId: string, itemIds: string[]): Promise<CreateJobResponse> {
        return request<CreateJobResponse>(`/api/v1/upload-batches/${batchId}/complete`, {
          method: "POST",
          body: JSON.stringify({ item_ids: itemIds })
        });
      },
      getJob(jobId: string): Promise<JobProgress> {
        return request<JobProgress>(`/api/v1/image/jobs/${jobId}`);
      },
      getHistory(): Promise<JobHistoryResponse> {
        return request<JobHistoryResponse>("/api/v1/image/jobs?limit=30");
      },
      async getResults(jobId: string, limit = 50, offset = 0, decision?: string): Promise<JobResults> {
        const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
        if (decision) params.set("decision", decision);
        const result = await request<BackendJobResults>(`/api/v1/image/jobs/${jobId}/results?${params}`);
        const objectKeys = result.images.flatMap((image) => [
          ...(image.files_expired ? [] : [image.original_object_key]),
          ...(image.files_expired || !image.enhanced_object_key ? [] : [image.enhanced_object_key])
        ]);
        const downloadUrls = await getDownloadUrlsSafely(objectKeys);
        const images = result.images.map((image) => ({
            image_id: image.image_id,
            rank: null,
            score: image.score ?? 0,
            decision: image.decision,
            original_url: downloadUrls.get(image.original_object_key),
            enhanced_url: image.enhanced_object_key
              ? downloadUrls.get(image.enhanced_object_key)
              : undefined,
            files_expired: image.files_expired,
            metrics: image.metrics ?? {},
            enhanced_metrics: image.enhanced_metrics ?? undefined,
            reasons: image.reasons,
            warnings: [],
            reject_codes: image.reject_codes,
            ai_tags: image.ai_tags ?? undefined,
            tagging_result: image.tagging_result ?? undefined
          }));
        return {
          job_id: result.job_id,
          summary: {
            total: result.total,
            selected: result.selected,
            rejected: result.rejected,
            not_selected: result.not_selected
          },
          result_total: result.result_total,
          limit: result.limit,
          offset: result.offset,
          images
        };
      },
      cancelJob(jobId: string): Promise<JobProgress> {
        return request<JobProgress>(`/api/v1/image/jobs/${jobId}/cancel`, { method: "POST" });
      },
      retryImage(jobId: string, imageId: string): Promise<JobProgress> {
        return request<JobProgress>(`/api/v1/image/jobs/${jobId}/images/${imageId}/retry`, { method: "POST" });
      },
      getFilterProfiles(): Promise<ProfileOption[]> {
        return request<ProfileOption[]>("/api/v1/filter-profiles");
      },
      getBeautifyProfiles(): Promise<ProfileOption[]> {
        return request<ProfileOption[]>("/api/v1/beautify-profiles");
      },
      getSimilarityProfiles(): Promise<ProfileOption[]> {
        return request<ProfileOption[]>("/api/v1/similarity-profiles");
      },
      getAIModelConfig(): Promise<AIModelConfig> {
        return request<AIModelConfig>("/api/v1/settings/ai-model");
      },
      updateAIModelConfig(payload: UpdateAIModelConfig): Promise<AIModelConfig> {
        return request<AIModelConfig>("/api/v1/settings/ai-model", {
          method: "PUT",
          body: JSON.stringify(payload)
        });
      },
      getLibraryTagTree(): Promise<LibraryTagNode[]> {
        return request<LibraryTagNode[]>("/api/v1/library/tag-tree");
      },
      createLibraryTagNode(payload: { name: string; parent_id?: string | null }): Promise<LibraryTagNode> {
        return request<LibraryTagNode>("/api/v1/library/tag-nodes", {
          method: "POST",
          body: JSON.stringify(payload)
        });
      },
      updateLibraryTagNode(nodeId: string, payload: { name?: string; sort_order?: number; status?: "active" | "disabled" }): Promise<LibraryTagNode> {
        return request<LibraryTagNode>(`/api/v1/library/tag-nodes/${nodeId}`, {
          method: "PATCH",
          body: JSON.stringify(payload)
        });
      },
      async deleteLibraryTagNode(nodeId: string): Promise<void> {
        await request<void>(`/api/v1/library/tag-nodes/${nodeId}`, { method: "DELETE" });
      },
      async createLibraryAsset(payload: { object_key: string; leaf_tag_node_id: string; original_filename?: string }): Promise<LibraryAsset> {
        return request<LibraryAsset>("/api/v1/library/assets", {
          method: "POST",
          body: JSON.stringify(payload)
        });
      },
      async getLibraryAssets(leafTagNodeId?: string | null): Promise<LibraryAssetList> {
        const query = leafTagNodeId ? `?leaf_tag_node_id=${encodeURIComponent(leafTagNodeId)}` : "";
        const result = await request<LibraryAssetList>(`/api/v1/library/assets${query}`);
        const items = await Promise.all(result.items.map(async (asset) => ({
          ...asset,
          preview_url: await getDownloadUrlSafely(asset.original_object_key)
        })));
        return { ...result, items };
      },
      updateLibraryAsset(assetId: string, payload: { leaf_tag_node_id?: string; status?: "active" | "disabled" }): Promise<LibraryAsset> {
        return request<LibraryAsset>(`/api/v1/library/assets/${assetId}`, {
          method: "PATCH",
          body: JSON.stringify(payload)
        });
      },
      getProcessingProfile(type: ProcessingProfileType, profileId: string): Promise<ProcessingProfile> {
        return request<ProcessingProfile>(`/api/v1/${type === "filter" ? "filter" : "beautify"}-profiles/${profileId}`);
      },
      previewProcessingProfile(type: ProcessingProfileType, instruction: string): Promise<ProfilePreview> {
        return request<ProfilePreview>(`/api/v1/${type === "filter" ? "filter" : "beautify"}-profiles/preview`, {
          method: "POST",
          body: JSON.stringify({ instruction })
        });
      },
      saveProcessingProfile(type: ProcessingProfileType, profileId: string | null, payload: SaveProcessingProfile): Promise<ProcessingProfile> {
        const root = `/api/v1/${type === "filter" ? "filter" : "beautify"}-profiles`;
        return request<ProcessingProfile>(profileId ? `${root}/${profileId}` : root, {
          method: profileId ? "PUT" : "POST",
          body: JSON.stringify(payload)
        });
      },
      async deleteProcessingProfile(type: ProcessingProfileType, profileId: string): Promise<void> {
        await request<void>(`/api/v1/${type === "filter" ? "filter" : "beautify"}-profiles/${profileId}`, { method: "DELETE" });
      },
      async deleteLibraryAsset(assetId: string): Promise<void> {
        await request<void>(`/api/v1/library/assets/${assetId}`, { method: "DELETE" });
      },
      reindexLibraryAsset(assetId: string): Promise<LibraryAsset> {
        return request<LibraryAsset>(`/api/v1/library/assets/${assetId}/reindex`, { method: "POST" });
      },
      getTagReviews(): Promise<TagReview[]> {
        return request<TagReview[]>("/api/v1/tag-reviews?limit=200");
      },
      decideTagReview(imageId: string, payload: { decision: "matched" | "unmatched"; matched_asset_id?: string | null }): Promise<TagReview> {
        return request<TagReview>(`/api/v1/tag-reviews/${imageId}/decision`, {
          method: "POST",
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

async function getDownloadUrlsSafely(objectKeys: string[]): Promise<Map<string, string>> {
  const uniqueKeys = Array.from(new Set(objectKeys)).slice(0, 100);
  if (!uniqueKeys.length) return new Map();
  try {
    const response = await request<{ items: Array<{ object_key: string; download_url: string }> }>(
      "/api/v1/uploads/presign-download-batch",
      { method: "POST", body: JSON.stringify({ object_keys: uniqueKeys }) }
    );
    return new Map(response.items.map((item) => [item.object_key, item.download_url]));
  } catch {
    return new Map();
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
