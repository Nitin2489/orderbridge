import { describe, expect, it } from "vitest";

import { MAX_RETRY_AFTER_SECONDS, parseRetryAfter } from "./retry-after";

const NOW = Date.parse("2026-11-07T10:00:00Z");

describe("parseRetryAfter", () => {
  it.each([
    ["0", 0],
    ["1", 1],
    ["120", 120],
    [" 900 ", 900],
    ["0003600", 3600],
  ])("reads delay-seconds %j as %i", (value, expected) => {
    expect(parseRetryAfter(value, NOW)).toBe(expected);
  });

  it("reads an HTTP date relative to now", () => {
    expect(parseRetryAfter("Sat, 07 Nov 2026 10:15:00 GMT", NOW)).toBe(900);
    expect(parseRetryAfter("Sat, 07 Nov 2026 10:00:00 GMT", NOW)).toBe(0);
  });

  it("rounds a fractional wait up to whole seconds", () => {
    expect(parseRetryAfter("Sat, 07 Nov 2026 10:00:01 GMT", NOW - 500)).toBe(2);
  });

  it("treats a date in the past as no wait", () => {
    expect(parseRetryAfter("Sat, 07 Nov 2026 09:00:00 GMT", NOW)).toBe(0);
  });

  it.each([
    ["86401", MAX_RETRY_AFTER_SECONDS],
    ["9".repeat(400), MAX_RETRY_AFTER_SECONDS],
    ["Thu, 01 Jan 2099 00:00:00 GMT", MAX_RETRY_AFTER_SECONDS],
  ])("caps %j at one day", (value, expected) => {
    expect(parseRetryAfter(value, NOW)).toBe(expected);
  });

  it.each([
    null,
    "",
    "-5",
    "1.5",
    "1e3",
    "soon",
    "120 seconds",
    "2026-11-07T10:15:00Z",
    "Saturday, 07-Nov-26 10:15:00 GMT",
    "Sat Nov  7 10:15:00 2026",
    "Sat, 07 Nov 2026 10:15:00 +0000",
  ])("ignores unusable value %j", (value) => {
    expect(parseRetryAfter(value, NOW)).toBeNull();
  });
});
