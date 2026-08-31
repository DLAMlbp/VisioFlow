import { describe, expect, it } from "vitest";
import type { ResultImage } from "../types";
import { decisionLabel, isTerminalStatus, normalizeDecision, processingReasons, rejectCodeLabel, statusLabel } from "./decision";

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

  it("builds precise rejection reasons from failed audit dimensions", () => {
    const image = {
      decision: "rejected",
      reject_codes: ["AI_FILTER_REJECTED"],
      reasons: ["图片总体不符合要求"],
      processing_standard_name: "非完工图片过滤",
      audit_dimensions: [
        { dimension: "构图、角度与空间感维度", passed: false, reason: "画面仅展示局部地面，缺少周边空间信息。" },
        { dimension: "内容相关性维度", passed: true, reason: "空鼓锤和标注能够确认这是瓦工验收节点。" }
      ]
    } as ResultImage;

    expect(processingReasons(image)).toEqual([
      "按「非完工图片过滤」标准，未通过「构图、角度与空间感」要求：画面仅展示局部地面，缺少周边空间信息。已识别的有效内容：空鼓锤和标注能够确认这是瓦工验收节点。"
    ]);
  });
});
