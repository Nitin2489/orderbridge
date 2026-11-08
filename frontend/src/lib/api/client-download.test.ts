import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, apiDownload, apiRequestWithStatus } from "./client";

function stubFetch(response: () => Response | Promise<Response>) {
  // Typed as fetch, so mock.calls are [input, init] without unused parameters.
  const fetchMock = vi.fn<typeof fetch>(async () => response());
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function jsonResponse(status: number, body: unknown) {
  return () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    });
}

async function caught(promise: Promise<unknown>): Promise<ApiError> {
  const error = await promise.catch((failure: unknown) => failure);
  expect(error).toBeInstanceOf(ApiError);
  return error as ApiError;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("apiRequestWithStatus", () => {
  it.each([201, 200])("returns the %i status with the parsed body", async (status) => {
    const fetchMock = stubFetch(jsonResponse(status, { id: "x" }));

    const result = await apiRequestWithStatus<{ id: string }>("/desk/exports", {
      method: "POST",
      body: { client_request_id: "abc" },
    });

    expect(result).toEqual({ status, data: { id: "x" } });
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/v1/desk/exports");
    expect(init?.method).toBe("POST");
    expect(init?.credentials).toBe("same-origin");
    expect(init?.body).toBe(JSON.stringify({ client_request_id: "abc" }));
  });

  it("throws the same ApiError as apiRequest for an error status", async () => {
    stubFetch(jsonResponse(422, { detail: "No confirmed orders are waiting for export." }));

    const error = await caught(apiRequestWithStatus("/desk/exports", { method: "POST" }));

    expect(error.status).toBe(422);
    expect(error.body).toEqual({ detail: "No confirmed orders are waiting for export." });
  });
});

describe("apiDownload", () => {
  it("returns the exact bytes and content type, through the same-origin proxy", async () => {
    const text = "﻿a,b\r\n1,2\r\n";
    const fetchMock = stubFetch(
      () => new Response(text, { headers: { "content-type": "text/csv; charset=utf-8" } }),
    );

    const file = await apiDownload("/desk/exports/abc/csv", { accept: "text/csv" });

    expect(new TextDecoder("utf-8", { ignoreBOM: true }).decode(file.bytes)).toBe(text);
    expect(file.contentType).toBe("text/csv; charset=utf-8");
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/v1/desk/exports/abc/csv");
    expect(init?.method).toBe("GET");
    expect(init?.credentials).toBe("same-origin");
    expect(init?.cache).toBe("no-store");
    expect(new Headers(init?.headers).get("Accept")).toBe("text/csv");
  });

  it("turns a JSON error into an ApiError", async () => {
    stubFetch(jsonResponse(404, { detail: "Export batch not found." }));

    const error = await caught(apiDownload("/desk/exports/abc/csv", { accept: "text/csv" }));

    expect(error.status).toBe(404);
    expect(error.message).toBe("Export batch not found.");
  });

  it("reports a network failure as status 0", async () => {
    stubFetch(() => {
      throw new TypeError("Failed to fetch");
    });

    const error = await caught(apiDownload("/desk/exports/abc/csv", { accept: "text/csv" }));

    expect(error.status).toBe(0);
  });

  it("refuses absolute and protocol-relative paths", async () => {
    stubFetch(jsonResponse(200, {}));

    await expect(apiDownload("//evil.example/x", { accept: "text/csv" })).rejects.toThrow(
      "API paths must be relative",
    );
    await expect(apiDownload("https://evil.example/x", { accept: "text/csv" })).rejects.toThrow(
      "API paths must be relative",
    );
  });
});

describe("apiRequest (unchanged)", () => {
  it("still rejects a non-JSON success", async () => {
    stubFetch(() => new Response("a,b", { headers: { "content-type": "text/csv" } }));

    const error = await caught(api.get("/desk/exports/eligible"));

    expect(error.message).toBe("Unexpected response from the server.");
  });

  it("still asks for JSON", async () => {
    const fetchMock = stubFetch(jsonResponse(200, { ok: true }));

    await api.get("/desk/exports/eligible");

    expect(new Headers(fetchMock.mock.calls[0]![1]?.headers).get("Accept")).toBe(
      "application/json",
    );
  });
});
