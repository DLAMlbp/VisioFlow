import { afterEach, describe, expect, it, vi } from "vitest";

import { api, clearAuthenticationSession, setAuthenticationRequiredHandler } from "./api";

describe("real API client", () => {
  afterEach(() => {
    clearAuthenticationSession();
    setAuthenticationRequiredHandler(null);
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
    expect(request.credentials).toBe("include");
  });

  it("keeps the session CSRF token for authenticated writes", async () => {
    const session = {
      user: {
        id: "user_admin",
        username: "admin",
        display_name: "系统管理员",
        role: "admin",
        is_active: true,
        last_login_at: "2026-09-10T00:00:00Z",
        created_at: "2026-09-10T00:00:00Z"
      },
      csrf_token: "csrf-test-token",
      expires_at: "2026-09-10T12:00:00Z"
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(session), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(session.user), {
        status: 201,
        headers: { "Content-Type": "application/json" }
      }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await api.login("admin", "secure-passphrase");
    await api.createUser({
      username: "operator",
      display_name: "操作员",
      password: "another-secure-passphrase",
      role: "operator"
    });
    await api.logout();

    const createRequest = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(new Headers(createRequest.headers).get("X-CSRF-Token")).toBe("csrf-test-token");
    expect(createRequest.credentials).toBe("include");
  });

  it("registers an operator account and keeps its new session", async () => {
    const session = {
      user: {
        id: "user_registered",
        username: "2940891991@qq.com",
        display_name: "新用户",
        role: "operator",
        is_active: true,
        last_login_at: "2026-09-10T00:00:00Z",
        created_at: "2026-09-10T00:00:00Z"
      },
      csrf_token: "registered-csrf-token",
      expires_at: "2026-09-10T12:00:00Z"
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(session), {
        status: 201,
        headers: { "Content-Type": "application/json" }
      }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await api.register({
      username: "2940891991@qq.com",
      display_name: "新用户",
      password: "secure-passphrase"
    });
    await api.logout();

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/v1/auth/register",
      expect.objectContaining({ method: "POST", credentials: "include" })
    );
    const registerRequest = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(registerRequest.body))).toEqual({
      username: "2940891991@qq.com",
      display_name: "新用户",
      password: "secure-passphrase"
    });
    expect(new Headers(registerRequest.headers).has("X-CSRF-Token")).toBe(false);
    const logoutRequest = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(new Headers(logoutRequest.headers).get("X-CSRF-Token")).toBe(
      "registered-csrf-token"
    );
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

  it("deletes all empty library groups in one request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ deleted_count: 4 }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    ));
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.deleteAllLibraryGroups();

    expect(result.deleted_count).toBe(4);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/library/groups",
      expect.objectContaining({ method: "DELETE" })
    );
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

  it("bulk deletes selected library assets", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ deleted_count: 2, failed_count: 0, failed_asset_ids: [] }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    ));
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.bulkDeleteLibraryAssets({ asset_ids: ["ast_1", "ast_2"] });

    expect(result.deleted_count).toBe(2);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/library/assets/bulk-delete",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ asset_ids: ["ast_1", "ast_2"] })
      })
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

  it("returns to login when an authenticated request expires", async () => {
    const onAuthenticationRequired = vi.fn();
    setAuthenticationRequiredHandler(onAuthenticationRequired);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "登录已过期，请重新登录" }),
      { status: 401, headers: { "Content-Type": "application/json" } }
    )));

    await expect(api.getProcessingStandards()).rejects.toThrow("登录已过期，请重新登录");
    expect(onAuthenticationRequired).toHaveBeenCalledOnce();
  });
});
