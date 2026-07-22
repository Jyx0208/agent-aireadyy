import { describe, expect, it } from "vitest";

import {
  canonicalDiscoveryPayloadFingerprint,
  canonicalDiscoveryPayloadJson,
} from "./strategy-fingerprint";

describe("canonical discovery execution fingerprint", () => {
  it("sorts object keys recursively, preserves arrays, and removes only root volatile fields", () => {
    expect(canonicalDiscoveryPayloadJson({
      z: [3, { b: true, a: null }],
      strategy_fingerprint: "drop-root",
      idempotency_key: "drop-root-too",
      nested: { strategy_fingerprint: "keep-nested", values: ["b", "a"] },
      numeric_keys: { "2": "two", "10": "ten" },
      grill_confirmed: true,
      a: { y: "micro", x: 1 },
    })).toBe(
      '{"a":{"x":1,"y":"micro"},"grill_confirmed":true,"nested":{"strategy_fingerprint":"keep-nested","values":["b","a"]},"numeric_keys":{"10":"ten","2":"two"},"z":[3,{"a":null,"b":true}]}',
    );
  });

  it("changes when an execution-affecting field changes but not for volatile root fields", async () => {
    const base = {
      acquisition_mode: "unknown",
      grill_confirmed: true,
      idempotency_key: "request-one",
      mixed_acquisition_policy: "review_mixed",
      strategy_fingerprint: "old-proof",
    };

    const original = await canonicalDiscoveryPayloadFingerprint(base);
    const reorderedAndVolatileOnly = await canonicalDiscoveryPayloadFingerprint({
      strategy_fingerprint: "another-old-proof",
      mixed_acquisition_policy: "review_mixed",
      idempotency_key: "request-two",
      grill_confirmed: true,
      acquisition_mode: "unknown",
    });
    const executionChange = await canonicalDiscoveryPayloadFingerprint({
      ...base,
      mixed_acquisition_policy: "reject_mixed",
    });
    const confirmationChange = await canonicalDiscoveryPayloadFingerprint({
      ...base,
      grill_confirmed: false,
    });

    expect(original).toMatch(/^[a-f0-9]{64}$/);
    expect(original).toBe("6efcfee288ca15ae5331a3e8aaa811b490f54ce461a11771ca785986ee5c21f1");
    expect(reorderedAndVolatileOnly).toBe(original);
    expect(executionChange).not.toBe(original);
    expect(confirmationChange).not.toBe(original);
  });
});
