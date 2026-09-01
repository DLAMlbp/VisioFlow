import { afterEach, describe, expect, it, vi } from "vitest";
import type { ResultImage } from "../types";
import { downloadResultImages, getDownloadableResultCount } from "./download";

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
});

describe("result image downloads", () => {
  it("triggers the preferred full-size URL in the original click task", () => {
    const clicked: Array<{ href: string; download: string }> = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push({ href: this.href, download: this.download });
    });

    const count = downloadResultImages([
      makeImage({
        original_download_url: "https://storage.test/original.jpg",
        enhanced_download_url: "https://storage.test/enhanced.jpg"
      })
    ]);

    expect(count).toBe(1);
    expect(clicked).toEqual([{
      href: "https://storage.test/enhanced.jpg",
      download: "image-01.jpg"
    }]);
  });

  it("counts only results that have a downloadable URL", () => {
    expect(getDownloadableResultCount([
      makeImage(),
      makeImage({ image_id: "image-02", original_url: "https://storage.test/preview.jpg" })
    ])).toBe(1);
  });
});
