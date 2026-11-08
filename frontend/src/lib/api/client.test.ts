import { describe, expect, it, vi } from "vitest";

import { ApiError, api } from "./client";

function respond(status: number, headers: Record<string, string> = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: "Too many requests. Please wait and try again." }), {
          status,
          headers: { "content-type": "application/json", ...headers },
        }),
    ),
  );
}

async function failure(): Promise<ApiError> {
  try {
    await api.post("/auth/request-otp", { phone_number: "+919000000001" });
  } catch (error) {
    if (error instanceof ApiError) {
      return error;
    }
    throw error;
  }
  throw new Error("expected the request to fail");
}

describe("ApiError.retryAfterSeconds", () => {
  it("keeps the status and the parsed Retry-After of a 429", async () => {
    respond(429, { "Retry-After": "120" });

    const error = await failure();

    expect(error.status).toBe(429);
    expect(error.retryAfterSeconds).toBe(120);
    expect(error.message).toBe("Too many requests. Please wait and try again.");
  });

  it("is null without a Retry-After header", async () => {
    respond(429);

    expect((await failure()).retryAfterSeconds).toBeNull();
  });

  it("is null for an unusable Retry-After header", async () => {
    respond(429, { "Retry-After": "later" });

    expect((await failure()).retryAfterSeconds).toBeNull();
  });

  it("defaults to null for errors built without one", () => {
    expect(new ApiError(0, "Offline", null).retryAfterSeconds).toBeNull();
  });
});
