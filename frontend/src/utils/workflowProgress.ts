import type { JobProgress } from "../types";

const TERMINAL_STATUSES = new Set(["completed", "partial_failed", "failed", "cancelled"]);

const stageCounts = [
  ["waiting"],
  ["classifying"],
  ["filtering"],
  ["beautify_planning", "beautifying"],
  ["content_analysis"],
  ["matching"]
] as const;

export interface WorkflowStageState {
  done: boolean;
  active: boolean;
}

/** Derive the visible workflow stage from backend state, not rounded percentages. */
export function workflowStageState(job: JobProgress, stageIndex: number): WorkflowStageState {
  if (TERMINAL_STATUSES.has(job.status)) {
    return { done: true, active: false };
  }

  const activeIndex = stageCounts.findIndex((keys) =>
    keys.some((key) => (job.stage_counts[key] ?? 0) > 0)
  );
  const currentIndex = activeIndex === -1 ? stageCounts.length : activeIndex;

  return {
    done: stageIndex < currentIndex,
    active: stageIndex === currentIndex
  };
}
