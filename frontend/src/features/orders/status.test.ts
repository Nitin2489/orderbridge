import { describe, expect, it } from "vitest";

import { FILTERABLE_STATUSES, hasConfirmedTotals, sourceLabel, statusInfo } from "./status";

describe("order statuses", () => {
  it.each([
    ["pending_review", "Pending review", "Waiting for order-desk review."],
    ["on_hold", "On hold", "Order is on hold and needs order-desk attention."],
    ["confirmed", "Confirmed", "Order confirmed."],
    ["rejected", "Rejected", "Order rejected."],
    ["exported", "Exported", "Order exported for billing."],
    ["draft", "Draft", "Draft order."],
  ])("describes %s", (status, label, description) => {
    expect(statusInfo(status)).toMatchObject({ label, description });
  });

  it("shows an unknown status as it is, with no invented description", () => {
    expect(statusInfo("in_transit")).toMatchObject({ label: "in transit", description: "" });
  });

  it("offers every status except draft as a filter", () => {
    expect(FILTERABLE_STATUSES).toEqual([
      "pending_review",
      "on_hold",
      "confirmed",
      "rejected",
      "exported",
    ]);
  });

  it("treats only confirmed and exported totals as final", () => {
    expect(hasConfirmedTotals("confirmed")).toBe(true);
    expect(hasConfirmedTotals("exported")).toBe(true);
    for (const status of ["pending_review", "on_hold", "rejected", "draft"]) {
      expect(hasConfirmedTotals(status)).toBe(false);
    }
  });

  it("labels order sources", () => {
    expect(sourceLabel("retailer_app")).toBe("Retailer app");
    expect(sourceLabel("ai_text")).toBe("Message capture");
    expect(sourceLabel("salesman")).toBe("Salesman");
  });
});
