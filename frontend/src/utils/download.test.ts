import { afterEach, describe, expect, it, vi } from "vitest";
import type { ResultImage } from "../types";
import { downloadResultArchive, getDownloadableResultCount } from "./download";

function makeImage(overrides: Partial<ResultImage> = {}): ResultImage {
  return {
    image_id: "image 01",
    rank: null,
    score: 90,
    decision: "selected",
    metrics: {},
    reasons: [],
    warnings: [],
    pipeline_stage: "completed",
    ...overrides
  };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("result image downloads", () => {
  it("downloads one ZIP archive", () => {
    const clicked: Array<{ href: string; download: string }> = [];
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:archive"),
      revokeObjectURL: vi.fn()
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push({ href: this.href, download: this.download });
    });

    downloadResultArchive(new Blob(["zip"]), "job_test_enhanced_images.zip");

    expect(clicked).toEqual([{
      href: "blob:archive",
      download: "job_test_enhanced_images.zip"
    }]);
  });

  it("counts only selected and non-expired results", () => {
    expect(getDownloadableResultCount([
      makeImage(),
      makeImage({
        image_id: "image-02",
        decision: "rejected"
      }),
      makeImage({
        image_id: "image-03",
        files_expired: true
      })
    ])).toBe(1);
  });
});
