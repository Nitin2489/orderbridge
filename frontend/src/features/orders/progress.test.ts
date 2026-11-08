import { describe, expect, it } from "vitest";

import { orderProgress } from "./progress";

const PLACED = "2026-11-07T10:00:00Z";
const CONFIRMED = "2026-11-08T05:00:00Z";

function steps(status: string, confirmed_at: string | null = null) {
  return orderProgress({ status, created_at: PLACED, confirmed_at })?.map((step) => [
    step.label,
    step.state,
    step.date,
  ]);
}

describe("orderProgress", () => {
  it("pending review: placed, then the order desk is reviewing", () => {
    expect(steps("pending_review")).toEqual([
      ["Placed", "done", "7 Nov 2026"],
      ["Order desk review", "current", null],
      ["Confirmation", "upcoming", null],
      ["Sent for billing", "upcoming", null],
    ]);
  });

  it("on hold: held at the order desk", () => {
    expect(steps("on_hold")?.[1]).toEqual(["On hold with the order desk", "held", null]);
  });

  it("confirmed: shows the confirmation date in India", () => {
    expect(steps("confirmed", CONFIRMED)).toEqual([
      ["Placed", "done", "7 Nov 2026"],
      ["Order desk review", "done", null],
      ["Confirmed by the order desk", "done", "8 Nov 2026"],
      ["Sent for billing", "upcoming", null],
    ]);
  });

  it("exported: every step is done", () => {
    expect(steps("exported", CONFIRMED)?.map(([, state]) => state)).toEqual([
      "done",
      "done",
      "done",
      "done",
    ]);
  });

  it("rejected: stops at the order desk", () => {
    expect(steps("rejected")).toEqual([
      ["Placed", "done", "7 Nov 2026"],
      ["Rejected by the order desk", "stopped", null],
    ]);
  });

  it("has no flow for drafts or unknown statuses", () => {
    expect(orderProgress({ status: "draft", created_at: PLACED, confirmed_at: null })).toBeNull();
    expect(orderProgress({ status: "teleported", created_at: PLACED, confirmed_at: null })).toBeNull();
  });

  it("never invents delivery or dispatch steps", () => {
    for (const status of ["pending_review", "on_hold", "confirmed", "exported", "rejected"]) {
      const labels = steps(status, CONFIRMED)?.map(([label]) => String(label)) ?? [];
      for (const label of labels) {
        expect(label).not.toMatch(/deliver|dispatch|shipped|out for|van/i);
      }
    }
  });
});
