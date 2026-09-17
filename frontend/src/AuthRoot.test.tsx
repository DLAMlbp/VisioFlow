import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AuthRoot from "./AuthRoot";
import { ApiError } from "./services/api";

const apiMocks = vi.hoisted(() => ({
  getCurrentSession: vi.fn(),
}));

vi.mock("./App", () => ({
  default: () => <div data-testid="workbench">工作台</div>,
}));

vi.mock("./services/api", () => {
  class MockApiError extends Error {
    constructor(message: string, readonly status: number) {
      super(message);
      this.name = "ApiError";
    }
  }

  return {
    ApiError: MockApiError,
    api: {
      getCurrentSession: apiMocks.getCurrentSession,
    },
    clearAuthenticationSession: vi.fn(),
    setAuthenticationRequiredHandler: vi.fn(),
  };
});

beforeEach(() => {
  apiMocks.getCurrentSession.mockReset().mockRejectedValue(new ApiError("请先登录", 401));
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("AuthRoot", () => {
  it("shows the login page instead of the workbench when no session exists", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);

    await act(async () => {
      root.render(<AuthRoot />);
    });

    expect(host.querySelector("h1")?.textContent).toBe("登录工作台");
    expect(host.querySelector('input[autocomplete="username"]')).not.toBeNull();
    expect(host.querySelector('input[autocomplete="current-password"]')).not.toBeNull();
    expect(host.querySelector('[data-testid="workbench"]')).toBeNull();

    await act(async () => root.unmount());
  });
});
