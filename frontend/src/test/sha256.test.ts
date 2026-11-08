import { describe, expect, it } from "vitest";

import { CSV_SHA256, CSV_TEXT } from "./export-fixtures";
import { sha256HexOf, testSubtleCrypto } from "./sha256";

/** The test-only SHA-256 must agree with independent implementations. */
describe("test SHA-256", () => {
  it.each([
    // FIPS 180-4 / NIST examples.
    ["", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"],
    ["abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"],
    [
      "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
      "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
    ],
    // Crosses several blocks (computed with Python's hashlib).
    ["a".repeat(1000), "41edece42d63e8d9bf515a9ba6932e1c20cbc9f5a5d134645adb5db1b9737ea3"],
  ])("hashes %j", (input, expected) => {
    expect(sha256HexOf(input)).toBe(expected);
  });

  it("reproduces the fixture CSV's checksum (computed with Python's hashlib)", () => {
    expect(CSV_SHA256).toBe("94971a87e7e8e1a18565aeed056dce5c9fb9acfb19bcdc08766122de7cbbbddd");
    expect(sha256HexOf(CSV_TEXT)).toBe(CSV_SHA256);
  });

  it("serves crypto.subtle.digest for ArrayBuffers and views", async () => {
    const bytes = new TextEncoder().encode("abc");
    const fromView = new Uint8Array(await testSubtleCrypto.digest("SHA-256", bytes));
    const fromBuffer = new Uint8Array(
      await testSubtleCrypto.digest({ name: "SHA-256" }, bytes.buffer as ArrayBuffer),
    );

    expect(fromView).toEqual(fromBuffer);
    expect(fromView[0]).toBe(0xba);
    await expect(testSubtleCrypto.digest("SHA-1", bytes)).rejects.toThrow("Unsupported");
  });
});
