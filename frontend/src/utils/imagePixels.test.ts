import { describe, expect, it } from "vitest";

import {
  imagePixelDisposition,
  MAX_DECODE_IMAGE_PIXELS,
  MAX_PROCESSING_IMAGE_PIXELS
} from "./imagePixels";

describe("imagePixelDisposition", () => {
  it("keeps images on the processing boundary", () => {
    expect(MAX_PROCESSING_IMAGE_PIXELS).toBe(12_000_000);
    expect(imagePixelDisposition(4000, 3000)).toBe("keep");
  });

  it("downscales common 4032 by 3024 phone photos", () => {
    expect(imagePixelDisposition(4032, 3024)).toBe("downscale");
  });

  it("rejects only images above the hard decode limit", () => {
    expect(MAX_DECODE_IMAGE_PIXELS).toBe(25_000_000);
    expect(imagePixelDisposition(6000, 5000)).toBe("reject");
  });
});
