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
      "无法连接服务，请确认正式后端已启动"
    );
  });
});
