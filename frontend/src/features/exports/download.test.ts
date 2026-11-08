import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import {
  BATCH_ID,
  CSV_SHA256,
  CSV_TEXT,
  csvResponse,
  savedText,
  withWebCrypto,
  withoutSubtleCrypto,
} from "@/test/export-fixtures";

import {
  ChecksumMismatchError,
  SessionChangedError,
  UnexpectedFileError,
  VerificationError,
  canVerifyDownloads,
  downloadErrorMessage,
  fetchBatchCsv,
  sha256Hex,
} from "./download";
import { batchCsvApiPath, csvFilename } from "./paths";

const batch = { id: BATCH_ID, batch_number: "EX-2026-000001", csv_sha256: CSV_SHA256 };

function stubFetch(response: () => Response) {
  // Typed as fetch, so mock.calls are [input, init] without unused parameters.
  const fetchMock = vi.fn<typeof fetch>(async () => response());
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("canVerifyDownloads", () => {
  it("is true with SubtleCrypto and false without it", () => {
    withWebCrypto();
    expect(canVerifyDownloads()).toBe(true);

    withoutSubtleCrypto();
    expect(canVerifyDownloads()).toBe(false);

    vi.stubGlobal("crypto", undefined);
    expect(canVerifyDownloads()).toBe(false);
  });
});

describe("sha256Hex", () => {
  it("hashes the exact bytes, BOM included", async () => {
    withWebCrypto();
    const bytes = new TextEncoder().encode(CSV_TEXT).buffer as ArrayBuffer;

    expect(await sha256Hex(bytes)).toBe(CSV_SHA256);
  });
});

describe("fetchBatchCsv", () => {
  it("fetches the CSV by batch id with the session cookie and returns verified bytes", async () => {
    withWebCrypto();
    const fetchMock = stubFetch(csvResponse());

    const file = await fetchBatchCsv(batch, { verify: true });

    expect(file.verified).toBe(true);
    expect(savedText(file.bytes)).toBe(CSV_TEXT);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe(`/api/v1/desk/exports/${BATCH_ID}/csv`);
    expect(init?.credentials).toBe("same-origin");
    expect(new Headers(init?.headers).get("Accept")).toContain("text/csv");
  });

  it("accepts an upper-case recorded checksum", async () => {
    withWebCrypto();
    stubFetch(csvResponse());

    const file = await fetchBatchCsv(
      { ...batch, csv_sha256: CSV_SHA256.toUpperCase() },
      { verify: true },
    );

    expect(file.verified).toBe(true);
  });

  it("throws on a checksum mismatch, so nothing can be saved", async () => {
    withWebCrypto();
    stubFetch(csvResponse(CSV_TEXT.replace("7776.00", "7777.00")));

    await expect(fetchBatchCsv(batch, { verify: true })).rejects.toBeInstanceOf(
      ChecksumMismatchError,
    );
  });

  it("throws when the checksum cannot be computed", async () => {
    vi.stubGlobal("crypto", {
      subtle: { digest: () => Promise.reject(new Error("not supported")) },
    });
    stubFetch(csvResponse());

    await expect(fetchBatchCsv(batch, { verify: true })).rejects.toBeInstanceOf(
      VerificationError,
    );
  });

  it("returns unverified bytes when verification is off (no Web Crypto)", async () => {
    withoutSubtleCrypto();
    stubFetch(csvResponse());

    const file = await fetchBatchCsv(batch, { verify: false });

    expect(file.verified).toBe(false);
    expect(savedText(file.bytes)).toBe(CSV_TEXT);
  });

  it("refuses a response that is not a CSV", async () => {
    withWebCrypto();
    stubFetch(csvResponse("<html></html>", "text/html"));

    await expect(fetchBatchCsv(batch, { verify: true })).rejects.toBeInstanceOf(
      UnexpectedFileError,
    );
    await expect(fetchBatchCsv(batch, { verify: false })).rejects.toBeInstanceOf(
      UnexpectedFileError,
    );
  });

  it("passes on API errors", async () => {
    withWebCrypto();
    stubFetch(
      () =>
        new Response(JSON.stringify({ detail: "Export batch not found." }), {
          status: 404,
          headers: { "content-type": "application/json" },
        }),
    );

    const error = await fetchBatchCsv(batch, { verify: true }).catch((failure: unknown) => failure);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(404);
  });
});

describe("downloadErrorMessage", () => {
  it.each([
    [new ChecksumMismatchError(), /did not match the checksum.*not saved/],
    [new VerificationError(), /could not be verified, so it was not saved/],
    [new UnexpectedFileError(), /did not return a CSV file/],
    [new ApiError(404, "x", null), /not found/],
    [new ApiError(403, "x", null), /refused/],
    [new ApiError(502, "x", null), /Something went wrong/],
    [new ApiError(0, "Unable to reach OrderBridge.", null), /Unable to reach OrderBridge/],
    [new Error("anything"), /could not be downloaded/],
  ])("explains %s", (error, message) => {
    expect(downloadErrorMessage(error)).toMatch(message);
  });

  it("shows nothing for a download abandoned by a session change", () => {
    expect(downloadErrorMessage(new SessionChangedError())).toBeNull();
  });
});

describe("paths", () => {
  it("builds the download path from the id", () => {
    expect(batchCsvApiPath(BATCH_ID)).toBe(`/desk/exports/${BATCH_ID}/csv`);
    expect(batchCsvApiPath("../x")).toBe("/desk/exports/..%2Fx/csv");
  });

  it("names the file like the backend and strips unsafe characters", () => {
    expect(csvFilename("EX-2026-000001")).toBe("orderbridge-tally-EX-2026-000001.csv");
    expect(csvFilename("../EX/1")).toBe("orderbridge-tally-EX1.csv");
    expect(csvFilename("///")).toBe("orderbridge-tally-export.csv");
  });
});
