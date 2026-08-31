import { describe, expect, it } from "vitest";
import { decisionLabel, isTerminalStatus, normalizeDecision, rejectCodeLabel, statusLabel } from "./decision";

describe("decision helpers", () => {
  it("maps backend decisions to Chinese labels", () => {
    expect(decisionLabel("selected")).toBe("已保留并美化");
    expect(decisionLabel("rejected")).toBe("分支过滤未通过");
  });

  it("detects terminal job statuses", () => {
    expect(isTerminalStatus("completed")).toBe(true);
    expect(isTerminalStatus("partial_failed")).toBe(true);
    expect(isTerminalStatus("analyzing")).toBe(false);
  });

  it("falls back unknown decisions to failed", () => {
    expect(normalizeDecision("unexpected")).toBe("failed");
  });

  it("maps job statuses to readable labels", () => {
    expect(statusLabel("enhancing")).toBe("自然美化中");
  });

  it("maps rejection codes to user-friendly reasons", () => {
    expect(rejectCodeLabel("IMAGE_TOO_SMALL")).toBe("图片尺寸不足");
    expect(rejectCodeLabel("EXTREME_BLUR")).toBe("图片严重模糊");
    expect(rejectCodeLabel("LOCAL_HEAVY_SHADOW")).toBe("局部阴影过重，暗部细节不足");
    expect(rejectCodeLabel("QUALITY_SCORE_TOO_LOW")).toBe("综合质量偏低，已保留供确认");
    expect(rejectCodeLabel("UNKNOWN_CODE")).toBe("不符合图片质量标准");
  });
});
