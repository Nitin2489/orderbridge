import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import { makeDeskDetail, makePreview } from "@/test/desk-fixtures";

import { allowedActions } from "./actions";
import { deskFailure, needsRefresh } from "./errors";

describe("allowedActions", () => {
  it.each([
    ["pending_review", true, { confirm: true, reject: true }],
    ["pending_review", false, { confirm: false, reject: true }],
    ["on_hold", true, { confirm: false, reject: true }],
    ["confirmed", true, { confirm: false, reject: false }],
    ["exported", true, { confirm: false, reject: false }],
    ["rejected", true, { confirm: false, reject: false }],
    ["draft", true, { confirm: false, reject: false }],
  ])("%s (retailer active: %s)", (status, isActive, expected) => {
    const order = makeDeskDetail({ status });
    order.retailer = { ...order.retailer, is_active: isActive };

    expect(allowedActions(order)).toEqual(expected);
  });
});

describe("deskFailure", () => {
  it("recognises an outdated preview by its body, not its message", () => {
    const preview = makePreview({ fingerprint: "c".repeat(64) });
    const failure = deskFailure(new ApiError(409, "anything", { detail: "x", preview }));

    expect(failure).toEqual({ kind: "outdated", preview });
    expect(needsRefresh(failure)).toBe(false);
  });

  it("treats any other 409 as a state change to read back", () => {
    const failure = deskFailure(new ApiError(409, "Retailer inactive", { detail: "whatever" }));

    expect(failure).toEqual({ kind: "state" });
    expect(needsRefresh(failure)).toBe(true);
  });

  it("separates line issues from other 422s", () => {
    const issues = [
      {
        line_number: 1,
        order_item_id: "i",
        product_id: "p",
        sku: "SKU",
        code: "insufficient_stock",
        message: "Not enough stock.",
      },
    ];
    expect(deskFailure(new ApiError(422, "x", { detail: "x", issues }))).toEqual({
      kind: "issues",
      issues,
    });
    expect(deskFailure(new ApiError(422, "x", { detail: [] }))).toEqual({ kind: "invalid" });
  });

  it.each([
    [new ApiError(0, "offline", null)],
    [new ApiError(502, "bad gateway", null)],
    [new Error("Unexpected response from the server.")],
  ])("treats %s as an unknown outcome", (error) => {
    const failure = deskFailure(error);
    expect(failure).toEqual({ kind: "uncertain" });
    expect(needsRefresh(failure)).toBe(true);
  });

  it("maps 404 and 403", () => {
    expect(deskFailure(new ApiError(404, "x", null))).toEqual({ kind: "not_found" });
    expect(deskFailure(new ApiError(403, "x", null))).toEqual({ kind: "forbidden" });
  });
});
