import type { Decision, JobStatus, ResultImage } from "../types";

function trimSentence(value: string): string {
  return value.trim().replace(/[。；;\s]+$/u, "");
}

export function processingReasons(image: ResultImage): string[] {
  const failed = (image.audit_dimensions ?? []).filter((dimension) => !dimension.passed);
  if (
    image.decision !== "rejected"
    || !image.reject_codes?.includes("AI_FILTER_REJECTED")
    || !failed.length
  ) {
    return image.reasons;
  }

  const details = failed.slice(0, 3).map((dimension) => {
    const label = dimension.dimension.trim().replace(/维度$/u, "").trim();
    return `「${label}」：${trimSentence(dimension.reason)}`;
  });
  const conclusion = failed.length === 1
    ? `未通过「${failed[0].dimension.trim().replace(/维度$/u, "").trim()}」要求：${trimSentence(failed[0].reason)}`
    : `未通过 ${failed.length} 项要求：${details.join("；")}${failed.length > details.length ? `；另有 ${failed.length - details.length} 项未通过` : ""}`;
  const context = (image.audit_dimensions ?? []).find((dimension) => (
    dimension.passed && /内容相关|施工阶段|场景/u.test(dimension.dimension)
  ));
  const prefix = image.processing_standard_name ? `按「${image.processing_standard_name}」标准，` : "";
  const contextText = context ? `。已识别的有效内容：${trimSentence(context.reason)}` : "";
  return [`${prefix}${conclusion}${contextText}。`];
}

export function decisionLabel(decision: Decision): string {
  const labels: Record<Decision, string> = {
    queued: "等待处理",
    analyzing: "分类与过滤中",
    filtered: "等待美化",
    beautify_planning: "美化规划中",
    enhancing: "美化中",
    enhanced: "等待素材匹配",
    selected: "已保留并美化",
    tagging: "素材匹配中",
    rejected: "分支过滤未通过",
    not_selected: "质量合格未入选",
    failed: "处理失败",
    cancelled: "已取消"
  };
  return labels[decision];
}

export function statusLabel(status: JobStatus): string {
  const labels: Record<JobStatus, string> = {
    created: "已创建",
    uploading: "上传中",
    queued: "排队中",
    processing: "流水处理中",
    analyzing: "智能分析中",
    ranking: "排序筛选中",
    enhancing: "自然美化中",
    tagging: "素材匹配中",
    completed: "已完成",
    partial_failed: "部分完成",
    failed: "处理失败",
    cancelled: "已取消"
  };
  return labels[status];
}

export function rejectCodeLabel(code: string): string {
  const labels: Record<string, string> = {
    IMAGE_TOO_SMALL: "图片尺寸不足",
    IMAGE_TOO_LARGE: "图片尺寸超出限制",
    EXTREME_BLUR: "图片严重模糊",
    EXTREME_OVEREXPOSURE: "图片严重过曝",
    EXTREME_UNDEREXPOSURE: "图片严重欠曝",
    LOCAL_HEAVY_SHADOW: "局部阴影过重，暗部细节不足",
    QUALITY_SCORE_TOO_LOW: "综合质量偏低，已保留供确认",
    SHARPNESS_SCORE_TOO_LOW: "清晰度偏低，已保留供确认",
    EXPOSURE_SCORE_TOO_LOW: "曝光偏离理想范围，已保留供确认",
    CONTRAST_SCORE_TOO_LOW: "对比度偏低，已保留供确认",
    NOISE_SCORE_TOO_LOW: "噪点偏高，已保留供确认",
    SOLID_COLOR: "图片内容过于单一",
    DUPLICATE_IMAGE: "与同批次其他图片重复或高度相似",
    AI_FILTER_REJECTED: "未通过对应分支过滤要求",
    COMPLETION_INVALID_OR_IRRELEVANT: "不是有效的真实室内装修照片",
    COMPLETION_INSUFFICIENT_EVIDENCE: "画面证据不足，无法可靠判断完工状态",
    COMPLETION_LOW_CONFIDENCE: "完工状态置信度不足"
  };
  return labels[code] ?? "不符合图片质量标准";
}

export function isTerminalStatus(status: JobStatus): boolean {
  return ["completed", "partial_failed", "failed", "cancelled"].includes(status);
}

export function normalizeDecision(value: string): Decision {
  if (["queued", "analyzing", "filtered", "beautify_planning", "enhancing", "enhanced", "selected", "rejected", "not_selected", "failed", "tagging", "cancelled"].includes(value)) {
    return value as Decision;
  }
  return "failed";
}
