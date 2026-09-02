import type { ResultImage } from "../types";

export function getDownloadableResultCount(images: ResultImage[]): number {
  return images.filter((image) => image.decision === "selected" && !image.files_expired).length;
}

export function downloadResultArchive(archive: Blob, filename: string): void {
  const url = URL.createObjectURL(archive);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.hidden = true;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
