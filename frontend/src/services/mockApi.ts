import type {
  CreateJobRequest,
  CreateJobResponse,
  AIModelConfig,
  JobHistoryResponse,
  JobProgress,
  JobResults,
  PresignRequest,
  PresignResponse,
  ProfileOption,
  ResultImage,
  UpdateAIModelConfig
} from "../types";

const filterProfiles: ProfileOption[] = [
  { id: "renovation_submission_v1", name: "装修照片基础筛选", description: "过滤尺寸不足、模糊、曝光异常和纯色图片" }
];

const beautifyProfiles: ProfileOption[] = [
  { id: "renovation_natural_v1", name: "装修照片自然美化", description: "轻微提亮、对比度、色彩和锐度增强，保留现场真实状态" }
];

let activeJob: {
  id: string;
  createdAt: number;
  total: number;
  request: CreateJobRequest;
} | null = null;

let aiModelConfig: AIModelConfig = {
  enabled: true,
  provider: "openai",
  base_url: "https://api.openai.com/v1",
  model: "gpt-5.6-luna",
  api_key_configured: true
};

export const mockApi = {
  async presignUpload(payload: PresignRequest): Promise<PresignResponse> {
    await wait(180);
    const safeName = payload.filename.replace(/[^\w.-]/g, "_");
    return {
      object_key: `uploads/mock/${crypto.randomUUID()}-${safeName}`,
      upload_url: `mock://upload/${safeName}`
    };
  },

  async uploadToStorage(_uploadUrl: string, _file: File, onProgress: (progress: number) => void): Promise<void> {
    for (const progress of [12, 34, 58, 79, 100]) {
      await wait(120);
      onProgress(progress);
    }
  },

  async createJob(payload: CreateJobRequest): Promise<CreateJobResponse> {
    await wait(320);
    activeJob = {
      id: `job_${Date.now().toString(36)}`,
      createdAt: Date.now(),
      total: payload.images.length,
      request: payload
    };
    return {
      job_id: activeJob.id,
      status: "queued",
      total: activeJob.total
    };
  },

  async getJob(jobId: string): Promise<JobProgress> {
    await wait(220);
    const job = requireJob(jobId);
    const elapsed = Date.now() - job.createdAt;
    const progress = Math.min(100, Math.round(elapsed / 65));
    const processed = Math.min(job.total, Math.floor((progress / 100) * job.total));
    const selected = Math.min(job.request.max_selected, Math.max(0, Math.floor(processed * 0.45)));
    const rejected = Math.max(0, processed - selected - Math.floor(processed * 0.2));

    return {
      job_id: job.id,
      status: statusForProgress(progress),
      progress,
      total: job.total,
      processed,
      selected,
      rejected
    };
  },

  async getHistory(): Promise<JobHistoryResponse> {
    await wait(160);
    if (!activeJob) return { total: 0, limit: 30, offset: 0, items: [] };
    const progress = await this.getJob(activeJob.id);
    return {
      total: 1,
      limit: 30,
      offset: 0,
      items: [
        {
          job_id: progress.job_id,
          status: progress.status,
          total: progress.total,
          processed: progress.processed,
          selected: progress.selected,
          rejected: progress.rejected,
          ai_tagging_model: aiModelConfig.enabled ? aiModelConfig.model : null,
          created_at: new Date(activeJob.createdAt).toISOString(),
          completed_at: progress.status === "completed" ? new Date().toISOString() : undefined
        }
      ]
    };
  },

  async getResults(jobId: string): Promise<JobResults> {
    await wait(280);
    const job = requireJob(jobId);
    const images = job.request.images.map((image, index) => createMockResult(image.object_key, index, job.request.max_selected));
    const selected = images.filter((image) => image.decision === "selected").length;
    const rejected = images.filter((image) => image.decision === "rejected").length;

    return {
      job_id: job.id,
      summary: {
        total: images.length,
        selected,
        rejected
      },
      images
    };
  },

  async getFilterProfiles(): Promise<ProfileOption[]> {
    await wait(120);
    return filterProfiles;
  },

  async getBeautifyProfiles(): Promise<ProfileOption[]> {
    await wait(120);
    return beautifyProfiles;
  },

  async getAIModelConfig(): Promise<AIModelConfig> {
    await wait(120);
    return aiModelConfig;
  },

  async updateAIModelConfig(payload: UpdateAIModelConfig): Promise<AIModelConfig> {
    await wait(180);
    aiModelConfig = {
      ...aiModelConfig,
      enabled: payload.enabled,
      base_url: payload.base_url,
      model: payload.model,
      api_key_configured: aiModelConfig.api_key_configured || Boolean(payload.api_key)
    };
    return aiModelConfig;
  }
};

function createMockResult(objectKey: string, index: number, maxSelected: number): ResultImage {
  const score = Math.max(38, Math.min(96, Math.round(92 - index * 4.8 + ((index % 3) - 1) * 4)));
  const selected = index < maxSelected && score >= 68;
  const decision = selected ? "selected" : "rejected";
  const imageId = `img_${String(index + 1).padStart(3, "0")}`;
  const metrics = {
    sharpness: clamp(score + 4 - (index % 4) * 3),
    exposure: clamp(score - 2 + (index % 5) * 2),
    contrast: clamp(score - 5 + (index % 3) * 5),
    noise: clamp(score - 6 + (index % 4) * 3)
  };

  return {
    image_id: imageId,
    rank: null,
    score,
    decision,
    original_url: `https://picsum.photos/seed/${encodeURIComponent(objectKey)}/900/680`,
    enhanced_url: selected ? `https://picsum.photos/seed/enhanced-${encodeURIComponent(objectKey)}/900/680` : undefined,
    metrics,
    enhanced_metrics: selected
      ? {
          sharpness: clamp(metrics.sharpness + 2),
          exposure: clamp(metrics.exposure + 3),
          contrast: clamp(metrics.contrast + 2),
          noise: clamp(metrics.noise + 3)
        }
      : undefined,
    reasons: selected ? ["通过基础质量标准", "已完成自然美化"] : ["未通过装修照片基础质量标准"],
    warnings: [],
    reject_codes: selected ? undefined : ["EXTREME_BLUR"]
  };
}

function statusForProgress(progress: number): JobProgress["status"] {
  if (progress >= 100) return "completed";
  if (progress >= 78) return "enhancing";
  if (progress >= 62) return "ranking";
  if (progress >= 20) return "analyzing";
  return "queued";
}

function requireJob(jobId: string) {
  if (!activeJob || activeJob.id !== jobId) {
    throw new Error("Job 不存在");
  }
  return activeJob;
}

function clamp(value: number): number {
  return Math.max(0, Math.min(100, Math.round(value)));
}

function wait(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}
