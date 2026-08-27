import type {
  CreateJobRequest,
  CreateJobResponse,
  JobHistoryResponse,
  JobProgress,
  JobResults,
  LibraryAsset,
  LibraryAssetList,
  LibraryTagNode,
  PresignRequest,
  PresignResponse,
  ProfileOption,
  ResultImage,
  TagReview,
  UploadBatchRegistration
} from "../types";

const filterProfiles: ProfileOption[] = [
  { id: "renovation_submission_v1", name: "装修照片基础筛选", description: "过滤尺寸不足、模糊、曝光异常和纯色图片" }
];

const beautifyProfiles: ProfileOption[] = [
  { id: "renovation_natural_v1", name: "装修照片自然美化", description: "轻微提亮、对比度、色彩和锐度增强，保留现场真实状态" }
];

const similarityProfiles: ProfileOption[] = [
  { id: "library_similarity_v2", name: "装修场景智能匹配（推荐）", description: "结合图片向量和中文场景特征，自动继承最相似素材的完整标签路径。" },
  { id: "library_similarity_v1", name: "装修场景严格匹配", description: "使用更高自动确认门槛，不确定结果进入人工复核。" }
];

let activeJob: {
  id: string;
  createdAt: number;
  total: number;
  request: CreateJobRequest;
  cancelled?: boolean;
} | null = null;

let pendingBatch: UploadBatchRegistration | null = null;

let libraryTagTree: LibraryTagNode[] = [
  {
    id: "tag_cases",
    parent_id: null,
    name: "完工案例",
    depth: 0,
    sort_order: 0,
    status: "active",
    asset_count: 0,
    children: [
      {
        id: "tag_old_house",
        parent_id: "tag_cases",
        name: "旧房翻新",
        depth: 1,
        sort_order: 0,
        status: "active",
        asset_count: 0,
        children: [
          { id: "tag_kitchen", parent_id: "tag_old_house", name: "厨房", depth: 2, sort_order: 0, status: "active", asset_count: 3, children: [] },
          { id: "tag_living", parent_id: "tag_old_house", name: "客餐厅", depth: 2, sort_order: 1, status: "active", asset_count: 2, children: [] }
        ]
      }
    ]
  },
  { id: "tag_reviews", parent_id: null, name: "业主好评", depth: 0, sort_order: 1, status: "active", asset_count: 1, children: [] }
];

let libraryAssets: LibraryAsset[] = [
  createMockLibraryAsset("ast_k1", "tag_kitchen", ["完工案例", "旧房翻新", "厨房"], 21),
  createMockLibraryAsset("ast_k2", "tag_kitchen", ["完工案例", "旧房翻新", "厨房"], 22),
  createMockLibraryAsset("ast_k3", "tag_kitchen", ["完工案例", "旧房翻新", "厨房"], 23),
  createMockLibraryAsset("ast_l1", "tag_living", ["完工案例", "旧房翻新", "客餐厅"], 31),
  createMockLibraryAsset("ast_l2", "tag_living", ["完工案例", "旧房翻新", "客餐厅"], 32),
  createMockLibraryAsset("ast_r1", "tag_reviews", ["业主好评"], 41)
];

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

  async createUploadBatch(payload: {
    filter_profile: string;
    beautify_profile: string;
    similarity_profile: string;
    enhance_level: number;
    max_selected: number;
    files: PresignRequest[];
  }): Promise<UploadBatchRegistration> {
    await wait(160);
    const batchId = `ubt_${Date.now().toString(36)}`;
    pendingBatch = {
      batch_id: batchId,
      status: "registered",
      expires_at: new Date(Date.now() + 86_400_000).toISOString(),
      items: payload.files.map((file) => ({
        id: crypto.randomUUID(),
        filename: file.filename,
        content_type: file.content_type,
        file_size: file.file_size,
        object_key: `uploads/mock/${crypto.randomUUID()}-${file.filename}`,
        upload_url: `mock://upload/${file.filename}`
      }))
    };
    return pendingBatch;
  },

  async completeUploadBatch(batchId: string, itemIds: string[]): Promise<CreateJobResponse> {
    if (!pendingBatch || pendingBatch.batch_id !== batchId) throw new Error("上传批次不存在");
    const selected = pendingBatch.items.filter((item) => itemIds.includes(item.id));
    return this.createJob({
      filter_profile: "renovation_submission_v1",
      beautify_profile: "renovation_natural_v1",
      similarity_profile: "library_similarity_v2",
      enhance_level: 1,
      max_selected: selected.length,
      images: selected.map((item) => ({ object_key: item.object_key }))
    });
  },

  async getJob(jobId: string): Promise<JobProgress> {
    await wait(220);
    const job = requireJob(jobId);
    if (job.cancelled) {
      return {
        job_id: job.id, status: "cancelled", progress: 100, total: job.total,
        processed: job.total, selected: 0, rejected: 0, not_selected: 0,
        tagging: 0, stage_counts: { cancelled: job.total }
      };
    }
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
      rejected,
      not_selected: 0,
      tagging: Math.max(0, processed - selected - rejected),
      stage_counts: { waiting: job.total - processed, completed: selected, rejected }
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
          not_selected: progress.not_selected,
          ai_tagging_model: null,
          created_at: new Date(activeJob.createdAt).toISOString(),
          completed_at: progress.status === "completed" ? new Date().toISOString() : undefined
        }
      ]
    };
  },

  async getResults(jobId: string, limit = 50, offset = 0, decision?: string): Promise<JobResults> {
    await wait(280);
    const job = requireJob(jobId);
    const images = job.request.images.map((image, index) => createMockResult(image.object_key, index, job.request.max_selected));
    const selected = images.filter((image) => image.decision === "selected").length;
    const rejected = images.filter((image) => image.decision === "rejected").length;

    const filtered = decision ? images.filter((image) => image.decision === decision) : images;
    return {
      job_id: job.id,
      summary: {
        total: images.length,
        selected,
        rejected,
        not_selected: 0
      },
      result_total: filtered.length,
      limit,
      offset,
      images: filtered.slice(offset, offset + limit)
    };
  },

  async cancelJob(jobId: string): Promise<JobProgress> {
    const job = requireJob(jobId);
    job.cancelled = true;
    return this.getJob(jobId);
  },

  async retryImage(jobId: string, _imageId: string): Promise<JobProgress> {
    const job = requireJob(jobId);
    job.cancelled = false;
    return this.getJob(jobId);
  },

  async getFilterProfiles(): Promise<ProfileOption[]> {
    await wait(120);
    return filterProfiles;
  },

  async getBeautifyProfiles(): Promise<ProfileOption[]> {
    await wait(120);
    return beautifyProfiles;
  },

  async getSimilarityProfiles(): Promise<ProfileOption[]> {
    await wait(100);
    return similarityProfiles;
  },

  async getLibraryTagTree(): Promise<LibraryTagNode[]> {
    await wait(140);
    return structuredClone(libraryTagTree);
  },

  async createLibraryTagNode(payload: { name: string; parent_id?: string | null }): Promise<LibraryTagNode> {
    await wait(180);
    const parent = payload.parent_id ? findTagNode(libraryTagTree, payload.parent_id) : null;
    const node: LibraryTagNode = {
      id: `tag_${crypto.randomUUID()}`,
      parent_id: parent?.id ?? null,
      name: payload.name,
      depth: parent ? parent.depth + 1 : 0,
      sort_order: parent?.children.length ?? libraryTagTree.length,
      status: "active",
      asset_count: 0,
      children: []
    };
    if (parent) parent.children.push(node);
    else libraryTagTree.push(node);
    return structuredClone(node);
  },

  async updateLibraryTagNode(nodeId: string, payload: { name?: string; sort_order?: number; status?: "active" | "disabled" }): Promise<LibraryTagNode> {
    await wait(160);
    const node = findTagNode(libraryTagTree, nodeId);
    if (!node) throw new Error("标签不存在");
    Object.assign(node, payload);
    sortTagTree(libraryTagTree);
    libraryAssets.forEach((asset) => {
      asset.tag_path = findTagPath(libraryTagTree, asset.leaf_tag_node_id);
    });
    return structuredClone(node);
  },

  async deleteLibraryTagNode(nodeId: string): Promise<void> {
    await wait(160);
    const node = findTagNode(libraryTagTree, nodeId);
    if (!node) throw new Error("标签不存在");
    if (node.children.length || libraryAssets.some((asset) => asset.leaf_tag_node_id === nodeId)) {
      throw new Error("存在下级标签或关联素材，不能删除");
    }
    removeTagNode(libraryTagTree, nodeId);
  },

  async createLibraryAsset(payload: { object_key: string; leaf_tag_node_id: string; original_filename?: string }): Promise<LibraryAsset> {
    await wait(220);
    const node = findTagNode(libraryTagTree, payload.leaf_tag_node_id);
    if (!node) throw new Error("标签不存在");
    const path = findTagPath(libraryTagTree, node.id);
    const asset = createMockLibraryAsset(`ast_${crypto.randomUUID()}`, node.id, path, libraryAssets.length + 50);
    asset.original_object_key = payload.object_key;
    asset.original_filename = payload.original_filename;
    libraryAssets.unshift(asset);
    node.asset_count += 1;
    return structuredClone(asset);
  },

  async getLibraryAssets(leafTagNodeId?: string | null): Promise<LibraryAssetList> {
    await wait(160);
    const items = leafTagNodeId ? libraryAssets.filter((asset) => asset.leaf_tag_node_id === leafTagNodeId) : libraryAssets;
    return { total: items.length, items: structuredClone(items) };
  },

  async updateLibraryAsset(assetId: string, payload: { leaf_tag_node_id?: string; status?: "active" | "disabled" }): Promise<LibraryAsset> {
    await wait(160);
    const asset = libraryAssets.find((entry) => entry.id === assetId);
    if (!asset) throw new Error("素材不存在");
    if (payload.status) asset.status = payload.status;
    if (payload.leaf_tag_node_id) {
      const previousNode = findTagNode(libraryTagTree, asset.leaf_tag_node_id);
      const nextNode = findTagNode(libraryTagTree, payload.leaf_tag_node_id);
      if (!nextNode) throw new Error("标签不存在");
      if (previousNode) previousNode.asset_count = Math.max(0, previousNode.asset_count - 1);
      nextNode.asset_count += 1;
      asset.leaf_tag_node_id = payload.leaf_tag_node_id;
      asset.tag_path = findTagPath(libraryTagTree, payload.leaf_tag_node_id);
    }
    return structuredClone(asset);
  },

  async reindexLibraryAsset(assetId: string): Promise<LibraryAsset> {
    await wait(180);
    const asset = libraryAssets.find((entry) => entry.id === assetId);
    if (!asset) throw new Error("素材不存在");
    asset.status = "pending";
    window.setTimeout(() => { asset.status = "active"; }, 900);
    return structuredClone(asset);
  },

  async getTagReviews(): Promise<TagReview[]> {
    await wait(120);
    return [];
  },

  async decideTagReview(imageId: string, payload: { decision: "matched" | "unmatched"; matched_asset_id?: string | null }): Promise<TagReview> {
    await wait(160);
    const asset = payload.matched_asset_id ? libraryAssets.find((entry) => entry.id === payload.matched_asset_id) : undefined;
    return {
      image_id: imageId,
      matched_asset_id: payload.decision === "matched" ? asset?.id ?? null : null,
      tag_path: payload.decision === "matched" ? asset?.tag_path ?? [] : [],
      similarity_score: null,
      feature_score: null,
      final_score: null,
      decision: payload.decision,
      message: payload.decision === "matched" ? "已匹配到相似图片素材" : "未识别到相似的图片素材",
      candidates: []
    };
  }
};

function createMockLibraryAsset(id: string, nodeId: string, tagPath: string[], seed: number): LibraryAsset {
  return {
    id,
    original_object_key: `library/mock/${id}.jpg`,
    original_filename: `${tagPath[tagPath.length - 1] ?? "素材"}-${seed}.jpg`,
    leaf_tag_node_id: nodeId,
    tag_path: tagPath,
    content_type: "image/jpeg",
    width: 1200,
    height: 900,
    status: "active",
    error_message: null,
    analysis: { summary: `${tagPath.join(" · ")}参考素材` },
    created_at: new Date(Date.now() - seed * 3600000).toISOString(),
    preview_url: `https://picsum.photos/seed/library-${seed}/640/480`
  };
}

function findTagNode(nodes: LibraryTagNode[], id: string): LibraryTagNode | null {
  for (const node of nodes) {
    if (node.id === id) return node;
    const child = findTagNode(node.children, id);
    if (child) return child;
  }
  return null;
}

function removeTagNode(nodes: LibraryTagNode[], id: string): boolean {
  const index = nodes.findIndex((node) => node.id === id);
  if (index >= 0) {
    nodes.splice(index, 1);
    return true;
  }
  return nodes.some((node) => removeTagNode(node.children, id));
}

function sortTagTree(nodes: LibraryTagNode[]): void {
  nodes.sort((left, right) => left.sort_order - right.sort_order || left.name.localeCompare(right.name));
  nodes.forEach((node) => sortTagTree(node.children));
}

function findTagPath(nodes: LibraryTagNode[], id: string, parents: string[] = []): string[] {
  for (const node of nodes) {
    const path = [...parents, node.name];
    if (node.id === id) return path;
    const childPath = findTagPath(node.children, id, path);
    if (childPath.length) return childPath;
  }
  return [];
}

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
    reject_codes: selected ? undefined : ["EXTREME_BLUR"],
    tagging_result: selected
      ? index % 3 === 2
        ? { decision: "unmatched", tag_path: [], matched_asset_id: null, similarity: 0.52, final_score: 0.49, message: "未识别到相似的图片素材" }
        : { decision: "matched", tag_path: ["完工案例", "旧房翻新", index % 2 ? "客餐厅" : "厨房"], matched_asset_id: index % 2 ? "ast_l1" : "ast_k1", similarity: 0.88, final_score: 0.86, message: "已匹配到相似图片素材" }
      : undefined
  };
}

function statusForProgress(progress: number): JobProgress["status"] {
  if (progress >= 100) return "completed";
  if (progress >= 88) return "tagging";
  if (progress >= 70) return "enhancing";
  if (progress >= 58) return "ranking";
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
