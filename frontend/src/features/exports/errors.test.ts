import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";

import { exportFailure, exportFailureMessage } from "./errors";

describe("exportFailure", () => {
  it.each([
    ["422 with a message: nothing waiting", new ApiError(422, "x", { detail: "No confirmed orders are waiting for export." }), "nothing_eligible"],
    ["422 validation issues", new ApiError(422, "x", { detail: [{ loc: ["body"], msg: "bad", type: "x" }] }), "rejected"],
    ["409 ineligible orders", new ApiError(409, "x", { detail: "x", ineligible: [] }), "conflict"],
    ["409 reused request id", new ApiError(409, "x", { detail: "x" }), "conflict"],
    ["403", new ApiError(403, "x", { detail: "x" }), "forbidden"],
    ["400", new ApiError(400, "x", null), "rejected"],
    ["network failure", new ApiError(0, "x", null), "uncertain"],
    ["500", new ApiError(500, "x", null), "uncertain"],
    ["502", new ApiError(502, "x", null), "uncertain"],
    ["an unreadable success", new Error("Unexpected response from the server."), "uncertain"],
  ])("classifies %s", (_label, error, kind) => {
    expect(exportFailure(error).kind).toBe(kind);
  });

  it("never matches the server's message text", () => {
    const sameText = "No confirmed orders are waiting for export.";
    expect(exportFailure(new ApiError(409, sameText, { detail: sameText })).kind).toBe("conflict");
  });
});

describe("exportFailureMessage", () => {
  it("says nothing was exported for every definite failure", () => {
    for (const kind of ["nothing_eligible", "conflict", "forbidden", "rejected"] as const) {
      expect(exportFailureMessage({ kind })).toMatch(/nothing was exported/i);
    }
  });

  it("says an uncertain outcome is unknown and can be retried safely", () => {
    const message = exportFailureMessage({ kind: "uncertain" });
    expect(message).toMatch(/could not tell whether the export was created/);
    expect(message).toMatch(/cannot export the same orders twice/);
  });
});
