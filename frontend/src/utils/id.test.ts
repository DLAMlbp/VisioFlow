import { afterEach, describe, expect, it, vi } from "vitest";
import { createClientId } from "./id";

afterEach(() => vi.unstubAllGlobals());

describe("createClientId", () => {
  it("works when randomUUID is unavailable on an HTTP origin", () => {
    vi.stubGlobal("crypto", {});
    const first = createClientId();
    const second = createClientId();
    expect(first).toBeTruthy();
    expect(second).not.toBe(first);
  });
});
