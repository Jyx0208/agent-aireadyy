const VOLATILE_ROOT_FIELDS = new Set([
  "strategy_fingerprint",
  "strategy_fingerprint_payload",
  "idempotency_key",
]);

function compareUnicodeKeys(left: string, right: string): number {
  const leftPoints = Array.from(left, (character) => character.codePointAt(0) ?? 0);
  const rightPoints = Array.from(right, (character) => character.codePointAt(0) ?? 0);
  for (let index = 0; index < Math.min(leftPoints.length, rightPoints.length); index += 1) {
    if (leftPoints[index] !== rightPoints[index]) return leftPoints[index] - rightPoints[index];
  }
  return leftPoints.length - rightPoints.length;
}

function canonicalJsonString(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJsonString).join(",")}]`;
  }
  if (value != null && typeof value === "object") {
    const source = value as Record<string, unknown>;
    return `{${Object.keys(source)
      .sort(compareUnicodeKeys)
      .map((key) => `${JSON.stringify(key)}:${canonicalJsonString(source[key])}`)
      .join(",")}}`;
  }

  const serialized = JSON.stringify(value);
  if (serialized == null) throw new TypeError("Discovery payload contains a non-JSON value");
  return serialized;
}

/** Canonical compact JSON shared with the backend execution-proof contract. */
export function canonicalDiscoveryPayloadJson(payload: Record<string, unknown>): string {
  const serialized = JSON.stringify(payload);
  if (!serialized) throw new TypeError("Discovery payload must be JSON serializable");
  const copied = JSON.parse(serialized) as Record<string, unknown>;
  for (const field of VOLATILE_ROOT_FIELDS) delete copied[field];
  return canonicalJsonString(copied);
}

export async function canonicalDiscoveryPayloadFingerprint(
  payload: Record<string, unknown>,
): Promise<string> {
  const canonical = canonicalDiscoveryPayloadJson(payload);
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
