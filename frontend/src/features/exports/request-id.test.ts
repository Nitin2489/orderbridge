import { describe, expect, it, vi } from "vitest";

import { newRequestId } from "./request-id";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("newRequestId", () => {
  it("uses crypto.randomUUID when available", () => {
    expect(newRequestId({ randomUUID: () => "11111111-1111-4111-8111-111111111111" })).toBe(
      "11111111-1111-4111-8111-111111111111",
    );
  });

  it("builds a version 4 UUID from getRandomValues outside secure contexts", () => {
    const environment = globalThis.crypto;
    const source = {
      getRandomValues: <T extends ArrayBufferView | null>(array: T) =>
        environment.getRandomValues(array as Uint8Array) as unknown as T,
    };

    const ids = new Set(Array.from({ length: 50 }, () => newRequestId(source)));

    expect(ids.size).toBe(50);
    for (const id of ids) {
      expect(id).toMatch(UUID_V4);
    }
  });

  it("sets the version and variant bits whatever the random bytes are", () => {
    const allOnes = {
      getRandomValues: <T extends ArrayBufferView | null>(array: T) => {
        (array as unknown as Uint8Array).fill(0xff);
        return array;
      },
    };

    expect(newRequestId(allOnes)).toBe("ffffffff-ffff-4fff-bfff-ffffffffffff");
  });

  it("refuses to invent an id from a crypto object without random sources", () => {
    expect(() => newRequestId({})).toThrow("cannot generate a request id");
  });

  it("refuses to invent an id when the browser has no global crypto", () => {
    // Passing undefined would not simulate this: a default parameter also
    // applies to an explicit undefined, so newRequestId(undefined) uses the
    // real globalThis.crypto. Remove the global instead, as the app's own
    // call, newRequestId(), would then see it.
    vi.stubGlobal("crypto", undefined);
    try {
      expect(globalThis.crypto).toBeUndefined();
      expect(() => newRequestId()).toThrow("cannot generate a request id");
    } finally {
      vi.unstubAllGlobals();
    }
    expect(globalThis.crypto).toBeDefined();
  });
});
