import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";

import { captureFailure, needsReload } from "./errors";

describe("captureFailure", () => {
  it.each([
    [new ApiError(409, "x", { detail: "x", current_version: 4 }), "stale"],
    [new ApiError(409, "x", { detail: "x", unresolved_lines: [1] }), "not_ready"],
    [new ApiError(409, "x", { detail: "x", line_number: 1, alias_text: "a", current_product_id: "p" }), "alias_conflict"],
    [new ApiError(409, "x", { detail: "x" }), "state"],
    [new ApiError(422, "x", { detail: "x", issues: [] }), "lines_invalid"],
    [new ApiError(422, "x", { detail: "x" }), "invalid"],
    [new ApiError(403, "x", null), "forbidden"],
    [new ApiError(404, "x", null), "not_found"],
    [new ApiError(429, "x", null), "rate_limited"],
    [new ApiError(0, "x", null), "uncertain"],
    [new ApiError(502, "x", null), "uncertain"],
    [new Error("Unexpected response from the server."), "uncertain"],
  ])("classifies %#", (error, kind) => {
    expect(captureFailure(error).kind).toBe(kind);
  });

  it("reloads after stale, state, uncertain and not-ready failures only", () => {
    expect(needsReload(captureFailure(new ApiError(0, "x", null)))).toBe(true);
    expect(needsReload(captureFailure(new ApiError(409, "x", { detail: "x" })))).toBe(true);
    expect(needsReload(captureFailure(new ApiError(422, "x", { detail: "x" })))).toBe(false);
  });
});
