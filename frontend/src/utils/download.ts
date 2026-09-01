import type { ResultImage } from "../types";

export function getResultDownloadUrl(image: ResultImage): string | undefined {
  return image.enhanced_download_url ?? image.original_download_url
    ?? image.enhanced_url ?? image.original_url;
}

export function getDownloadableResultCount(images: ResultImage[]): number {
  return images.filter((image) => Boolean(getResultDownloadUrl(image))).length;
}

export function downloadResultImages(images: ResultImage[]): number {
  const downloadable = images.flatMap((image) => {
    const url = getResultDownloadUrl(image);
    return url ? [{ image, url }] : [];
  });

  downloadable.forEach(({ image, url }) => triggerDownload(url, getResultDownloadFilename(image, url)));

  return downloadable.length;
}

export function getResultDownloadFilename(image: ResultImage, sourceUrl: string): string {
  const safeImageId = image.image_id.replace(/[^a-zA-Z0-9._-]+/g, "-") || "image";
  const extension = extensionFromUrl(sourceUrl) ?? "jpg";
  return safeImageId.toLowerCase().endsWith(`.${extension}`)
    ? safeImageId
    : `${safeImageId}.${extension}`;
}

function triggerDownload(url: string, filename: string): void {
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.hidden = true;
  document.body.append(link);
  link.click();
  link.remove();
}

function extensionFromUrl(sourceUrl: string): string | undefined {
  try {
    const match = new URL(sourceUrl, window.location.href).pathname.match(/\.([a-zA-Z0-9]+)$/);
    const extension = match?.[1]?.toLowerCase();
    return extension && ["jpg", "jpeg", "png", "webp"].includes(extension)
      ? (extension === "jpeg" ? "jpg" : extension)
      : undefined;
  } catch {
    return undefined;
  }
}
