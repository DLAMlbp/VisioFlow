import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LibraryWorkspace } from "./LibraryWorkspace";

const apiMocks = vi.hoisted(() => ({
  deleteLibraryGroup: vi.fn(),
  getLibraryAssets: vi.fn(),
  getLibraryGroups: vi.fn(),
}));

vi.mock("./services/api", () => ({
  api: {
    ...apiMocks,
  },
}));

const group = {
  id: "grp_test",
  tags: ["日常", "施工报价"],
  tag_key: "日常|施工报价",
  sort_order: 0,
  status: "active" as const,
  asset_count: 2,
  created_at: "2026-09-17T00:00:00Z",
  updated_at: "2026-09-17T00:00:00Z",
};

beforeEach(() => {
  apiMocks.deleteLibraryGroup.mockReset().mockResolvedValue(undefined);
  apiMocks.getLibraryAssets.mockReset().mockResolvedValue({ items: [], total: 0 });
  apiMocks.getLibraryGroups.mockReset()
    .mockResolvedValueOnce([group])
    .mockResolvedValue([]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("LibraryWorkspace tag group deletion", () => {
  it("enables the header trash button for a selected group and deletes it after confirmation", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);

    await act(async () => {
      root.render(<LibraryWorkspace onMessage={() => undefined} />);
    });

    const deleteButton = host.querySelector<HTMLButtonElement>(
      'button[aria-label="删除选中的标签组合"]'
    );
    expect(deleteButton).not.toBeNull();
    expect(deleteButton?.disabled).toBe(true);

    const groupButton = Array.from(host.querySelectorAll<HTMLButtonElement>(
      ".library-group-row"
    )).find((button) => button.textContent?.includes("施工报价"));
    expect(groupButton).not.toBeUndefined();

    await act(async () => {
      groupButton?.click();
    });
    expect(deleteButton?.disabled).toBe(false);

    await act(async () => {
      deleteButton?.click();
    });

    expect(window.confirm).toHaveBeenCalledWith(
      "确认删除标签组合“日常、施工报价”并永久删除组内全部 2 张图片（包括原图和缩略图）？此操作无法恢复。"
    );
    expect(apiMocks.deleteLibraryGroup).toHaveBeenCalledWith("grp_test");
    expect(deleteButton?.disabled).toBe(true);

    await act(async () => root.unmount());
  });
});
