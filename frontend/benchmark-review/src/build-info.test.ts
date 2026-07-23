import { describe, expect, it } from "vitest";

import { APP_BUILD_INFO, buildInfoLabel, buildInfoTitle } from "./build-info";

describe("application build identity", () => {
  it("injects a non-empty, inspectable build stamp", () => {
    expect(APP_BUILD_INFO.version.trim()).not.toBe("");
    expect(APP_BUILD_INFO.revision.trim()).not.toBe("");
    expect(Number.isNaN(Date.parse(APP_BUILD_INFO.builtAt))).toBe(false);
    expect(buildInfoLabel()).toContain(`v${APP_BUILD_INFO.version}`);
    expect(buildInfoTitle()).toContain(APP_BUILD_INFO.builtAt);
  });
});
