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
  ProcessingStandard,
  ProfilePreview,
  SaveProcessingProfile,
  SaveProcessingStandard,
  LibraryAsset,
  LibraryAssetGroup,
  LibraryAssetList,
  LogoRedactionUpdate,
  TagReview,
  UpdateAIModelConfig,
  UploadBatchRegistration
} from "../types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";
const REQUEST_TIMEOUT_MS = 30_000;
const DOWNLOAD_REQUEST_TIMEOUT_MS = 5 * 60_000;
const LIBRARY_ASSET_PAGE_SIZE = 200;

interface BackendResultImage {
  image_id: string;
  decision: Decision;
  score: number | null;
  original_object_key: string;
  enhanced_object_key: string | null;
  original_preview_object_key?: string | null;
  enhanced_preview_object_key?: string | null;
  files_expired: boolean;
  reject_codes: string[];
  reasons: string[];
  metrics: JobResults["images"][number]["metrics"] | null;
  enhanced_metrics: JobResults["images"][number]["metrics"] | null;
  ai_tags: JobResults["images"][number]["ai_tags"] | null;
  library_tags: JobResults["images"][number]["library_tags"] | null;
  tagging_result: JobResults["images"][number]["tagging_result"] | null;
  processing_standard_id: string | null;
  processing_standard_name: string | null;
  activation_reason: string | null;
  audit_dimensions: Array<{
    dimension: string;
    passed: boolean;
    reason: string;
  }>;
  completion: JobResults["images"][number]["completion"] | null;
  classification: JobResults["images"][number]["classification"] | null;
  beautify: JobResults["images"][number]["beautify"] | null;
  routed_filter_profile_id: string | null;
  routed_filter_profile_version: number | null;
  pipeline_stage: string;
  classification_status: string | null;
  filter_status: string | null;
  beautify_status: string | null;
  analysis_status: string | null;
  embedding_status: string | null;
  match_status: string | null;
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
  if (init?.body) headers.set("Content-Type", "application/json");
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers,
      signal: controller.signal
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("服务响应超时，请稍后重试");
    }
    throw new Error("无法连接服务，请确认正式后端已启动");
  } finally {
    window.clearTimeout(timeout);
  }

  if (!response.ok) {
    const raw = await response.text();
    let detail = raw;
    try {
      const payload = JSON.parse(raw) as { detail?: string; message?: string };
      detail = payload.detail ?? payload.message ?? raw;
    } catch {
      // Keep the plain-text response when the server did not return JSON.
    }
    throw new Error(detail || `请求失败：${response.status}`);
  }

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function requestBlob(path: string, init?: RequestInit): Promise<Blob> {
  const headers = new Headers(init?.headers);
  if (init?.body) headers.set("Content-Type", "application/json");
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), DOWNLOAD_REQUEST_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers,
      signal: controller.signal
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("压缩包准备超时，请稍后重试");
    }
    throw new Error("无法连接服务，请确认正式后端已启动");
  } finally {
    window.clearTimeout(timeout);
  }

  if (!response.ok) {
    const raw = await response.text();
    let detail = raw;
    try {
      const payload = JSON.parse(raw) as { detail?: string; message?: string };
      detail = payload.detail ?? payload.message ?? raw;
    } catch {
      // Keep the plain-text response when the server did not return JSON.
    }
    throw new Error(detail || `请求失败：${response.status}`);
  }

  return response.blob();
}

export const api = {
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
        filter_route?: CreateJobRequest["filter_route"];
        processing_standards?: string[];
        beautify_profile?: string;
        filter_enabled: boolean;
        beautify_enabled: boolean;
        similarity_enabled: boolean;
        unmatched_standard_policy: "reject";
        enhance_level: number;
        max_selected?: number;
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
      async getResults(jobId: string, limit = 50, offset = 0, filter?: string): Promise<JobResults> {
        const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
        if (filter === "completed" || filter === "non_completed") params.set("completion_label", filter);
        else if (filter === "review") params.set("review_required", "true");
        else if (filter) params.set("decision", filter);
        const result = await request<BackendJobResults>(`/api/v1/image/jobs/${jobId}/results?${params}`);
        const objectKeys = result.images.flatMap((image) => [
          ...(image.files_expired ? [] : [
            image.original_object_key,
            image.original_preview_object_key ?? image.original_object_key
          ]),
          ...(image.files_expired || !image.enhanced_object_key ? [] : [
            image.enhanced_object_key,
            image.enhanced_preview_object_key ?? image.enhanced_object_key
          ])
        ]);
        const downloadUrls = await getDownloadUrlsSafely(objectKeys);
        const images = result.images.map((image) => ({
            image_id: image.image_id,
            rank: null,
            score: image.score ?? 0,
            decision: image.decision,
            original_url: downloadUrls.get(
              image.original_preview_object_key ?? image.original_object_key
            ),
            enhanced_url: image.enhanced_object_key
              ? downloadUrls.get(
                  image.enhanced_preview_object_key ?? image.enhanced_object_key
                )
              : undefined,
            original_download_url: downloadUrls.get(image.original_object_key),
            enhanced_download_url: image.enhanced_object_key
              ? downloadUrls.get(image.enhanced_object_key)
              : undefined,
            files_expired: image.files_expired,
            metrics: image.metrics ?? {},
            enhanced_metrics: image.enhanced_metrics ?? undefined,
            reasons: image.reasons,
            warnings: [],
            reject_codes: image.reject_codes,
            ai_tags: image.ai_tags ?? undefined,
            library_tags: image.library_tags ?? image.tagging_result ?? undefined,
            tagging_result: image.tagging_result ?? undefined,
            processing_standard_id: image.processing_standard_id,
            processing_standard_name: image.processing_standard_name,
            activation_reason: image.activation_reason,
            audit_dimensions: image.audit_dimensions ?? [],
            completion: image.completion ?? undefined,
            classification: image.classification ?? undefined,
            beautify: image.beautify ?? undefined,
            routed_filter_profile_id: image.routed_filter_profile_id,
            routed_filter_profile_version: image.routed_filter_profile_version,
            pipeline_stage: image.pipeline_stage,
            classification_status: image.classification_status,
            filter_status: image.filter_status,
            beautify_status: image.beautify_status,
            analysis_status: image.analysis_status,
            embedding_status: image.embedding_status,
            match_status: image.match_status
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
      downloadSelectedResultsArchive(jobId: string, imageIds?: string[]): Promise<Blob> {
        return requestBlob(`/api/v1/image/jobs/${jobId}/downloads/selected`, {
          method: "POST",
          body: JSON.stringify({ image_ids: imageIds })
        });
      },
      cancelJob(jobId: string): Promise<JobProgress> {
        return request<JobProgress>(`/api/v1/image/jobs/${jobId}/cancel`, { method: "POST" });
      },
      retryImage(jobId: string, imageId: string): Promise<JobProgress> {
        return request<JobProgress>(`/api/v1/image/jobs/${jobId}/images/${imageId}/retry`, { method: "POST" });
      },
      updateLogoRedaction(
        jobId: string,
        imageId: string,
        boxes: [number, number, number, number][]
      ): Promise<LogoRedactionUpdate> {
        return request<LogoRedactionUpdate>(
          `/api/v1/image/jobs/${jobId}/images/${imageId}/redaction/logos`,
          { method: "PUT", body: JSON.stringify({ boxes }) }
        );
      },
      getFilterProfiles(): Promise<ProfileOption[]> {
        return request<ProfileOption[]>("/api/v1/filter-profiles");
      },
      getBeautifyProfiles(): Promise<ProfileOption[]> {
        return request<ProfileOption[]>("/api/v1/beautify-profiles");
      },
      getCompletionProfiles(): Promise<ProfileOption[]> {
        return request<ProfileOption[]>("/api/v1/completion-profiles");
      },
      getProcessingStandards(): Promise<ProfileOption[]> {
        return request<ProfileOption[]>("/api/v1/processing-standards");
      },
      getProcessingStandard(profileId: string): Promise<ProcessingStandard> {
        return request<ProcessingStandard>(`/api/v1/processing-standards/${profileId}`);
      },
      previewProcessingStandard(payload: {
        classification_rule: string;
        filter_rule: string;
        priority: number;
        is_fallback: boolean;
      }): Promise<ProfilePreview> {
        return request<ProfilePreview>("/api/v1/processing-standards/preview", {
          method: "POST",
          body: JSON.stringify(payload)
        });
      },
      saveProcessingStandard(profileId: string | null, payload: SaveProcessingStandard): Promise<ProcessingStandard> {
        return request<ProcessingStandard>(
          profileId ? `/api/v1/processing-standards/${profileId}` : "/api/v1/processing-standards",
          { method: profileId ? "PUT" : "POST", body: JSON.stringify(payload) }
        );
      },
      async deleteProcessingStandard(profileId: string): Promise<void> {
        await request<void>(`/api/v1/processing-standards/${profileId}`, { method: "DELETE" });
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
      getLibraryGroups(): Promise<LibraryAssetGroup[]> {
        return request<LibraryAssetGroup[]>("/api/v1/library/groups");
      },
      createLibraryGroup(payload: { tags: string[]; sort_order?: number }): Promise<LibraryAssetGroup> {
        return request<LibraryAssetGroup>("/api/v1/library/groups", {
          method: "POST",
          body: JSON.stringify(payload)
        });
      },
      updateLibraryGroup(groupId: string, payload: { tags?: string[]; sort_order?: number; status?: "active" | "disabled" }): Promise<LibraryAssetGroup> {
        return request<LibraryAssetGroup>(`/api/v1/library/groups/${groupId}`, {
          method: "PATCH",
          body: JSON.stringify(payload)
        });
      },
      async deleteLibraryGroup(groupId: string): Promise<void> {
        await request<void>(`/api/v1/library/groups/${groupId}`, { method: "DELETE" });
      },
      async createLibraryAsset(payload: { object_key: string; group_id: string; original_filename?: string }): Promise<LibraryAsset> {
        return request<LibraryAsset>("/api/v1/library/assets", {
          method: "POST",
          body: JSON.stringify(payload)
        });
      },
      async getLibraryAssets(groupId?: string | null): Promise<LibraryAssetList> {
        const items: LibraryAsset[] = [];
        let total = 0;
        do {
          const params = new URLSearchParams({
            limit: String(LIBRARY_ASSET_PAGE_SIZE),
            offset: String(items.length)
          });
          if (groupId) params.set("group_id", groupId);
          const page = await request<LibraryAssetList>(`/api/v1/library/assets?${params}`);
          total = page.total;
          items.push(...page.items);
          if (!page.items.length) break;
        } while (items.length < total);

        const previewKeys = items.map(
          (asset) => asset.thumbnail_object_key ?? asset.original_object_key
        );
        const previewUrls = await getDownloadUrlsSafely(previewKeys);
        return {
          total,
          items: items.map((asset) => ({
            ...asset,
            preview_url: previewUrls.get(
              asset.thumbnail_object_key ?? asset.original_object_key
            )
          }))
        };
      },
      updateLibraryAsset(assetId: string, payload: { group_id?: string; status?: "active" | "disabled" }): Promise<LibraryAsset> {
        return request<LibraryAsset>(`/api/v1/library/assets/${assetId}`, {
          method: "PATCH",
          body: JSON.stringify(payload)
        });
      },
      getProcessingProfile(type: ProcessingProfileType, profileId: string): Promise<ProcessingProfile> {
        return request<ProcessingProfile>(`/api/v1/${profileRoot(type)}/${profileId}`);
      },
      previewProcessingProfile(type: ProcessingProfileType, instruction: string): Promise<ProfilePreview> {
        return request<ProfilePreview>(`/api/v1/${profileRoot(type)}/preview`, {
          method: "POST",
          body: JSON.stringify({ instruction })
        });
      },
      saveProcessingProfile(type: ProcessingProfileType, profileId: string | null, payload: SaveProcessingProfile): Promise<ProcessingProfile> {
        const root = `/api/v1/${profileRoot(type)}`;
        return request<ProcessingProfile>(profileId ? `${root}/${profileId}` : root, {
          method: profileId ? "PUT" : "POST",
          body: JSON.stringify(payload)
        });
      },
      async deleteProcessingProfile(type: ProcessingProfileType, profileId: string): Promise<void> {
        await request<void>(`/api/v1/${profileRoot(type)}/${profileId}`, { method: "DELETE" });
      },
      async deleteLibraryAsset(assetId: string): Promise<void> {
        await request<void>(`/api/v1/library/assets/${assetId}`, { method: "DELETE" });
      },
      reindexLibraryAsset(assetId: string): Promise<LibraryAsset> {
        return request<LibraryAsset>(`/api/v1/library/assets/${assetId}/reindex`, { method: "POST" });
      },
      async getTagReviews(): Promise<TagReview[]> {
        const reviews = await request<TagReview[]>("/api/v1/tag-reviews?limit=200");
        const previewKeys = reviews.flatMap((review) => (
          review.candidates.flatMap((candidate) => candidate.preview_object_key ? [candidate.preview_object_key] : [])
        ));
        const previewUrls = await getDownloadUrlsSafely(previewKeys);
        return reviews.map((review) => ({
          ...review,
          candidates: review.candidates.map((candidate) => ({
            ...candidate,
            preview_url: candidate.preview_object_key
              ? previewUrls.get(candidate.preview_object_key)
              : undefined
          }))
        }));
      },
      decideTagReview(imageId: string, payload: { decision: "matched" | "unmatched"; matched_asset_id?: string | null }): Promise<TagReview> {
        return request<TagReview>(`/api/v1/tag-reviews/${imageId}/decision`, {
          method: "POST",
          body: JSON.stringify(payload)
        });
      }
    };

function profileRoot(type: ProcessingProfileType): string {
  return `${type}-profiles`;
}

async function getDownloadUrl(objectKey: string): Promise<string> {
  const response = await request<{ download_url: string }>("/api/v1/uploads/presign-download", {
    method: "POST",
    body: JSON.stringify({ object_key: objectKey })
  });
  return response.download_url;
}

const DOWNLOAD_URL_CACHE_TTL_MS = 10 * 60 * 1000;
const downloadUrlCache = new Map<string, { url: string; expiresAt: number }>();

function getCachedDownloadUrl(objectKey: string): string | undefined {
  const cached = downloadUrlCache.get(objectKey);
  if (!cached) return undefined;
  if (cached.expiresAt <= Date.now()) {
    downloadUrlCache.delete(objectKey);
    return undefined;
  }
  return cached.url;
}

function cacheDownloadUrl(objectKey: string, url: string): void {
  downloadUrlCache.set(objectKey, {
    url,
    expiresAt: Date.now() + DOWNLOAD_URL_CACHE_TTL_MS
  });
}

async function getDownloadUrlSafely(objectKey: string): Promise<string | undefined> {
  const cached = getCachedDownloadUrl(objectKey);
  if (cached) return cached;
  try {
    const url = await getDownloadUrl(objectKey);
    cacheDownloadUrl(objectKey, url);
    return url;
  } catch {
    return undefined;
  }
}

async function getDownloadUrlsSafely(objectKeys: string[]): Promise<Map<string, string>> {
  const uniqueKeys = Array.from(new Set(objectKeys));
  if (!uniqueKeys.length) return new Map();
  const urls = new Map<string, string>();
  const missingKeys: string[] = [];
  uniqueKeys.forEach((objectKey) => {
    const cached = getCachedDownloadUrl(objectKey);
    if (cached) urls.set(objectKey, cached);
    else missingKeys.push(objectKey);
  });
  if (!missingKeys.length) return urls;
  try {
    for (let index = 0; index < missingKeys.length; index += 100) {
      const response = await request<{ items: Array<{ object_key: string; download_url: string }> }>(
        "/api/v1/uploads/presign-download-batch",
        {
          method: "POST",
          body: JSON.stringify({ object_keys: missingKeys.slice(index, index + 100) })
        }
      );
      response.items.forEach((item) => {
        cacheDownloadUrl(item.object_key, item.download_url);
        urls.set(item.object_key, item.download_url);
      });
    }
    return urls;
  } catch {
    return urls;
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
