/* @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { IntentSpecPanel } from "./IntentSpecPanel";
import { applyLocalParse, applyRecommendedDefaults } from "./grill-tree";

afterEach(cleanup);

describe("IntentSpecPanel repository query selection", () => {
  it("lets the user deselect wording variants before confirming search", () => {
    const spec = applyRecommendedDefaults(
      applyLocalParse("Find as many human immunopeptidomics projects as possible"),
    );
    const onConfirm = vi.fn();
    let selectedSearchTerms = [
      "immunopeptidomics",
      "immunopeptidome",
      "HLA ligandome",
      "MHC ligandome",
      "HLA peptidome",
      "MHC peptidome",
      "HLA class I ligandome",
      "HLA class II ligandome",
      "immunopeptides",
      "HLA ligands",
      "MHC ligands",
      "MHC-associated peptides",
    ];
    const onSelectedSearchTermsChange = vi.fn((terms: string[]) => {
      selectedSearchTerms = terms;
    });

    const { rerender } = render(
      <IntentSpecPanel
        spec={spec}
        phase="awaiting_confirm"
        onConfirm={onConfirm}
        selectedSearchTerms={selectedSearchTerms}
        onSelectedSearchTermsChange={onSelectedSearchTermsChange}
        onApplyDefaults={() => undefined}
      />,
    );

    expect(screen.getByRole("button", { name: "✓ immunopeptidome" })).toBeTruthy();
    const hlaLigandome = screen.getByRole("button", { name: "✓ HLA ligandome" });
    fireEvent.click(hlaLigandome);
    rerender(
      <IntentSpecPanel
        spec={spec}
        phase="awaiting_confirm"
        onConfirm={onConfirm}
        selectedSearchTerms={selectedSearchTerms}
        onSelectedSearchTermsChange={onSelectedSearchTermsChange}
        onApplyDefaults={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "确认主题词，开始搜" }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
    const selected = onConfirm.mock.calls[0][0] as string[];
    expect(selected).toContain("immunopeptidome");
    expect(selected).not.toContain("HLA ligandome");
  });

  it("lets the user choose which confirmed phrase is searched as the core term", () => {
    const spec = applyRecommendedDefaults(
      applyLocalParse("Find as many human immunopeptidomics projects as possible"),
    );
    let selectedSearchTerms = ["immunopeptidomics", "HLA class I ligandome"];
    const onSelectedSearchTermsChange = vi.fn((terms: string[]) => {
      selectedSearchTerms = terms;
    });
    const { rerender } = render(
      <IntentSpecPanel
        spec={spec}
        phase="awaiting_confirm"
        onConfirm={() => undefined}
        selectedSearchTerms={selectedSearchTerms}
        onSelectedSearchTermsChange={onSelectedSearchTermsChange}
        onApplyDefaults={() => undefined}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "设为核心" }));
    rerender(
      <IntentSpecPanel
        spec={spec}
        phase="awaiting_confirm"
        onConfirm={() => undefined}
        selectedSearchTerms={selectedSearchTerms}
        onSelectedSearchTermsChange={onSelectedSearchTermsChange}
        onApplyDefaults={() => undefined}
      />,
    );

    expect(selectedSearchTerms[0]).toBe("HLA class I ligandome");
    expect(screen.getByText("核心词")).toBeTruthy();
  });
});
