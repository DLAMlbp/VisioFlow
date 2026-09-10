import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";

describe("real API client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("returns processing standards from the formal backend", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([
      { id: "std_finished", name: "完工图", description: "完工实景", version: 1 }
    ]), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const standards = await api.getProcessingStandards();

    expect(standards[0]?.id).toBe("std_finished");
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(request.headers).has("Content-Type")).toBe(false);
  });

  it("loads flat library groups with one tag set per image group", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify([
      {
        id: "grp_living",
        tags: ["客厅", "现代风格", "完工"],
        sort_order: 0,
        status: "active",
        asset_count: 4
      }
    ]), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    const groups = await api.getLibraryGroups();

    expect(groups[0]?.tags).toEqual(["客厅", "现代风格", "完工"]);
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/library/groups", expect.any(Object));
  });

  it("loads a 50-item history page with its offset", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      total: 257,
      limit: 50,
      offset: 100,
      items: []
    }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);

    await api.getHistory(50, 100);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/image/jobs?limit=50&offset=100",
      expect.any(Object)
    );
  });

  it("loads every library asset page and reports the backend total", async () => {
    const firstPage = Array.from({ length: 200 }, (_, index) => ({
      id: `asset_${index}`,
      original_object_key: `uploads/asset_${index}.jpg`,
      thumbnail_object_key: null,
      original_filename: `asset_${index}.jpg`,
      group_id: "grp_all",
      tags: ["施工"],
      status: "active",
      created_at: "2026-09-01T00:00:00Z"
    }));
    const secondPage = Array.from({ length: 42 }, (_, index) => ({
      ...firstPage[index],
      id: `asset_${index + 200}`,
      original_object_key: `uploads/asset_${index + 200}.jpg`,
      original_filename: `asset_${index + 200}.jpg`
    }));
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("offset=200")) {
        return Promise.resolve(new Response(JSON.stringify({ total: 242, items: secondPage }), {
          status: 200,
          headers: { "Content-Type": "application/json" }
        }));
      }
      if (url.includes("presign-download-batch")) {
        return Promise.resolve(new Response(JSON.stringify({ items: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" }
        }));
      }
      return Promise.resolve(new Response(JSON.stringify({ total: 242, items: firstPage }), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.getLibraryAssets();

    expect(result.total).toBe(242);
    expect(result.items).toHaveLength(242);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("limit=200&offset=200"),
      expect.any(Object)
    );
  });

  it("requeues failed library assets in the selected group", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ queued_count: 7 }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    ));
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.reindexFailedLibraryAssets("grp_test");

    expect(result.queued_count).toBe(7);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/library/assets/reindex-failed?group_id=grp_test",
      expect.objectContaining({ method: "POST" })
    );
  });

  it("shows the backend detail instead of a raw JSON response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "服务端 API_KEY 未配置" }),
      { status: 503, headers: { "Content-Type": "application/json" } }
    )));

    await expect(api.getProcessingStandards()).rejects.toThrow("服务端 API_KEY 未配置");
  });

  it("reports a clear connection error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("connection refused")));

    await expect(api.getProcessingStandards()).rejects.toThrow(
      "暂时无法连接服务，正在自动重试"
    );
  });
});
