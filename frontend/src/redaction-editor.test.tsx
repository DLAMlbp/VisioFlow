import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LogoBoxEditor } from "./App";

const redaction = {
  watermark: { image_size: [1080, 1440] },
  logos: {
    image_size: [1080, 1440],
    boxes: [[20, 30, 220, 130]],
    confidences: [0.96],
  },
};

afterEach(() => {
  document.body.innerHTML = "";
});

describe("LogoBoxEditor", () => {
  it("deletes an automatic box, draws a replacement, and saves pixel coordinates", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const onSave = vi.fn(async () => undefined);
    const root = createRoot(host);
    await act(async () => {
      root.render(
        <LogoBoxEditor
          redaction={redaction}
          saving={false}
          onSave={onSave}
          onCancel={() => undefined}
        />
      );
    });

    const buttons = Array.from(host.querySelectorAll("button"));
    await act(async () => {
      buttons.find((button) => button.textContent?.includes("删除选中"))?.click();
      buttons.find((button) => button.textContent?.includes("手动画框"))?.click();
    });

    const svg = host.querySelector("svg") as SVGSVGElement;
    vi.spyOn(svg, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      right: 600,
      bottom: 800,
      width: 600,
      height: 800,
      toJSON: () => ({}),
    });
    Object.defineProperties(svg, {
      setPointerCapture: { value: vi.fn() },
      hasPointerCapture: { value: vi.fn(() => false) },
      releasePointerCapture: { value: vi.fn() },
    });

    await act(async () => {
      svg.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, clientX: 100, clientY: 100, pointerId: 1 }));
      svg.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: 200, clientY: 200, pointerId: 1 }));
      svg.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, clientX: 200, clientY: 200, pointerId: 1 }));
    });
    await act(async () => {
      buttons.find((button) => button.textContent?.includes("保存并重新生成"))?.click();
    });

    expect(onSave).toHaveBeenCalledOnce();
    expect(onSave).toHaveBeenCalledWith([[180, 180, 360, 360]]);
    await act(async () => root.unmount());
  });
});
