import { describe, expect, it } from "vitest";

import { dateRange, startOfIndiaDay, startOfNextIndiaDay } from "./filters";

describe("queue date filters", () => {
  it("uses the start of the day in India", () => {
    expect(startOfIndiaDay("2026-11-05")).toBe("2026-11-05T00:00:00+05:30");
  });

  it("ends a range at the start of the following day (exclusive)", () => {
    expect(startOfNextIndiaDay("2026-11-07")).toBe("2026-11-08T00:00:00+05:30");
    expect(startOfNextIndiaDay("2026-12-31")).toBe("2027-01-01T00:00:00+05:30");
    expect(startOfNextIndiaDay("2028-02-28")).toBe("2028-02-29T00:00:00+05:30");
  });

  it("builds inclusive ranges, either end optional", () => {
    expect(dateRange("2026-11-05", "2026-11-07")).toEqual({
      createdFrom: "2026-11-05T00:00:00+05:30",
      createdTo: "2026-11-08T00:00:00+05:30",
      error: null,
    });
    expect(dateRange("", "2026-11-07").createdFrom).toBeNull();
    expect(dateRange("2026-11-05", "").createdTo).toBeNull();
    expect(dateRange("", "")).toEqual({ createdFrom: null, createdTo: null, error: null });
  });

  it("refuses an inverted or invalid range instead of sending it", () => {
    expect(dateRange("2026-11-08", "2026-11-07")).toEqual({
      createdFrom: null,
      createdTo: null,
      error: "The start date must not be after the end date.",
    });
    expect(dateRange("2026-02-30", "").error).toBe("Enter valid dates.");
  });
});
