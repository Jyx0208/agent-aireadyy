/* @vitest-environment jsdom */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BatchPanel } from "./BatchPanel";

vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);

afterEach(() => cleanup());

describe("BatchPanel", () => {
  it("defaults new batch work to the complete workflow", () => {
    render(<BatchPanel batchId="" onBatchId={vi.fn()} />);

    const mode = screen.getByLabelText("运行模式") as HTMLSelectElement;
    expect(mode.value).toBe("full");
    expect(
      screen.getByRole("option", {
        name: "完整工作流（下载 → 转换 → 执行 → 打包）",
      }),
    ).toBeTruthy();
  });
});
