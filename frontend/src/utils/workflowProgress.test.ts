import { describe, expect, it } from "vitest";
import type { JobProgress } from "../types";
import { workflowStageState } from "./workflowProgress";

function job(stage_counts: Record<string, number>, status: JobProgress["status"] = "processing"): JobProgress {
  return {
    job_id: "job-test",
    status,
    progress: 18,
    total: 11,
    processed: 0,
    selected: 0,
    rejected: 0,
    not_selected: 0,
    tagging: 0,
    stage_counts
  };
}

describe("workflowStageState", () => {
  it("keeps classification active when progress happens to be 18 percent", () => {
    const progress = job({ classifying: 11 });

    expect(workflowStageState(progress, 0)).toEqual({ done: true, active: false });
    expect(workflowStageState(progress, 1)).toEqual({ done: false, active: true });
    expect(workflowStageState(progress, 2)).toEqual({ done: false, active: false });
  });

  it("shows the earliest unfinished stage while images span multiple stages", () => {
    const progress = job({ classifying: 2, beautifying: 9 });

    expect(workflowStageState(progress, 1).active).toBe(true);
    expect(workflowStageState(progress, 3).active).toBe(false);
  });

  it("marks every stage complete for terminal jobs", () => {
    const progress = job({ failed: 11 }, "partial_failed");

    for (let stageIndex = 0; stageIndex < 7; stageIndex += 1) {
      expect(workflowStageState(progress, stageIndex)).toEqual({ done: true, active: false });
    }
  });
});
