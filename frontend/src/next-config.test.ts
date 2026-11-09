import { describe, expect, it } from "vitest";

import nextConfig, { SECURITY_HEADERS } from "../next.config";

describe("next.config security headers", () => {
  it("sends the three security headers on every path", async () => {
    const rules = await nextConfig.headers!();

    expect(rules).toEqual([{ source: "/:path*", headers: SECURITY_HEADERS }]);
    expect(Object.fromEntries(SECURITY_HEADERS.map(({ key, value }) => [key, value]))).toEqual({
      "X-Frame-Options": "DENY",
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "strict-origin-when-cross-origin",
    });
  });

  it("keeps the powered-by header off", () => {
    expect(nextConfig.poweredByHeader).toBe(false);
  });
});
