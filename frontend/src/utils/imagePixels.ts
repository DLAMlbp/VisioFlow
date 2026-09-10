export const MAX_PROCESSING_IMAGE_PIXELS = 12_000_000;
export const MAX_DECODE_IMAGE_PIXELS = 25_000_000;

export type ImagePixelDisposition = "keep" | "downscale" | "reject";

export function imagePixelDisposition(width: number, height: number): ImagePixelDisposition {
  const pixels = width * height;
  if (!Number.isSafeInteger(pixels) || width <= 0 || height <= 0) return "reject";
  if (pixels > MAX_DECODE_IMAGE_PIXELS) return "reject";
  if (pixels > MAX_PROCESSING_IMAGE_PIXELS) return "downscale";
  return "keep";
}

export async function readImageDimensions(file: File): Promise<{ width: number; height: number }> {
  const bitmap = await createImageBitmap(file);
  try {
    return { width: bitmap.width, height: bitmap.height };
  } finally {
    bitmap.close();
  }
}
