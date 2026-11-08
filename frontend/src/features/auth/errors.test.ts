import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";

import { isRateLimited, requestOtpErrorMessage, verifyOtpErrorMessage } from "./errors";

function tooMany(retryAfterSeconds: number | null): ApiError {
  return new ApiError(429, "Too many requests.", null, retryAfterSeconds);
}

describe("429 messages", () => {
  it.each([
    [requestOtpErrorMessage],
    [verifyOtpErrorMessage],
  ])("names the wait from Retry-After (%#)", (message) => {
    expect(message(tooMany(900))).toBe(
      "Too many attempts. Please wait 15 minutes and try again.",
    );
    expect(message(tooMany(45))).toBe("Too many attempts. Please wait 45 seconds and try again.");
  });

  it.each([[requestOtpErrorMessage], [verifyOtpErrorMessage]])(
    "falls back to a general wait without Retry-After (%#)",
    (message) => {
      expect(message(tooMany(null))).toBe(
        "Too many attempts. Please wait a few minutes and try again.",
      );
      expect(message(tooMany(0))).toBe(
        "Too many attempts. Please wait a few minutes and try again.",
      );
    },
  );

  it("recognises only 429 as rate limited", () => {
    expect(isRateLimited(tooMany(10))).toBe(true);
    expect(isRateLimited(new ApiError(401, "No", null))).toBe(false);
    expect(isRateLimited(new Error("x"))).toBe(false);
  });
});
