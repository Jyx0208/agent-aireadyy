import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const styles = readFileSync(new URL("./styles.css", import.meta.url), "utf8");

const rule = (selector: string) => {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return styles.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`))?.[1] || "";
};

describe("running task scroll ownership", () => {
  it("keeps the command bar and tabs in normal flow so the task viewport owns scrolling", () => {
    expect(rule(".current-task-workspace--running")).toContain(
      "overflow-y: auto",
    );
    expect(rule(".ops-command-bar")).not.toContain("position: sticky");
    expect(rule(".ops-console > .cds--tabs")).not.toContain("position: sticky");
  });

  it("allows wheel scrolling to chain from the live event feed to the task viewport", () => {
    expect(rule(".ops-event-feed")).not.toContain(
      "overscroll-behavior: contain",
    );
  });
});
