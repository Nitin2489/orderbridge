import { vi } from "vitest";

import type {
  ExportBatchDetail,
  ExportBatchPage,
  ExportBatchSummary,
} from "@/features/exports/types";

import { testSubtleCrypto } from "./sha256";

/** Export API shapes for tests only. Money is backend-style decimal strings. */

export const BATCH_ID = "55555555-5555-4555-8555-555555555555";
export const NEW_BATCH_ID = "66666666-6666-4666-8666-666666666666";

/** A small CSV as the backend stores it: BOM, header, CRLF. */
export const CSV_TEXT =
  "﻿export_batch_number,order_number,net_amount\r\n" +
  "EX-2026-000001,OB-2026-000123,7776.00\r\n";
/** SHA-256 of CSV_TEXT's UTF-8 bytes, computed with Python's hashlib (see sha256.test.ts). */
export const CSV_SHA256 = "94971a87e7e8e1a18565aeed056dce5c9fb9acfb19bcdc08766122de7cbbbddd";

export function makeBatchSummary(overrides: Partial<ExportBatchSummary> = {}): ExportBatchSummary {
  const id = overrides.id ?? BATCH_ID;
  return {
    id,
    batch_number: "EX-2026-000001",
    created_at: "2026-11-07T10:00:00Z",
    created_by_name: "Order Desk Operator",
    order_count: 2,
    net_total: "17621.50",
    csv_sha256: CSV_SHA256,
    csv_url: `/api/v1/desk/exports/${id}/csv`,
    ...overrides,
  };
}

export function makeBatchDetail(overrides: Partial<ExportBatchDetail> = {}): ExportBatchDetail {
  return {
    ...makeBatchSummary(overrides),
    orders: [
      {
        id: "44444444-4444-4444-8444-444444444444",
        order_number: "OB-2026-000123",
        status: "exported",
        retailer_business_name: "Sharma General Store",
        confirmed_at: "2026-11-07T09:00:00Z",
        net_total: "7776.00",
      },
      {
        id: "44444444-4444-4444-8444-555555555555",
        order_number: "OB-2026-000124",
        status: "exported",
        retailer_business_name: "Gupta Kirana Store",
        confirmed_at: "2026-11-07T09:30:00Z",
        net_total: "9845.50",
      },
    ],
    ...overrides,
  };
}

export function batchPage(items: ExportBatchSummary[], total = items.length): ExportBatchPage {
  return { items, total, limit: 20, offset: 0 };
}

/** A CSV download response, as GET /desk/exports/{id}/csv sends it. */
export function csvResponse(
  text: string = CSV_TEXT,
  contentType = "text/csv; charset=utf-8",
): () => Response {
  return () =>
    new Response(text, {
      status: 200,
      headers: {
        "content-type": contentType,
        "content-disposition": 'attachment; filename="orderbridge-tally-EX-2026-000001.csv"',
        "cache-control": "no-store",
      },
    });
}

/** The test environment's own crypto (jsdom: random values, no SubtleCrypto). */
const environmentCrypto = globalThis.crypto;

/*
 * Stand-ins for Crypto's getRandomValues and randomUUID, delegating to the
 * environment's real crypto. They are written with explicit signatures
 * instead of being typed as Crypto["getRandomValues"] / Crypto["randomUUID"]:
 * this project merges the DOM and @types/node crypto declarations, so those
 * member types are overloaded (no contextual parameter type) and the
 * global's randomUUID() is typed as a plain string.
 */

/**
 * Fills `array` with random bytes in place and returns it, as
 * Crypto.getRandomValues does. The environment fills a Uint8Array view of
 * the same memory, which every crypto typing accepts. The constraint
 * includes null (as older DOM typings do) so the function satisfies each
 * declaration; null is rejected as the real API rejects it.
 */
function randomValues<T extends ArrayBufferView | null>(array: T): T {
  if (array === null) {
    throw new TypeError("getRandomValues requires an ArrayBufferView.");
  }
  environmentCrypto.getRandomValues(
    new Uint8Array(array.buffer, array.byteOffset, array.byteLength),
  );
  return array;
}

/** The UUID shape Crypto.randomUUID promises. */
type Uuid = `${string}-${string}-${string}-${string}-${string}`;

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function isUuid(value: string): value is Uuid {
  return UUID_PATTERN.test(value);
}

/** The environment's randomUUID(), checked (not asserted) to be a UUID. */
function randomUUID(): Uuid {
  const value: string = environmentCrypto.randomUUID();
  if (!isUuid(value)) {
    throw new Error("The test environment's crypto.randomUUID() did not return a UUID.");
  }
  return value;
}

/** A browser with full Web Crypto (secure context), SHA-256 included. */
export function withWebCrypto(): void {
  vi.stubGlobal("crypto", { getRandomValues: randomValues, randomUUID, subtle: testSubtleCrypto });
}

/**
 * A browser without SubtleCrypto (an insecure context or an old browser):
 * random values exist, SHA-256 does not.
 */
export function withoutSubtleCrypto(): void {
  vi.stubGlobal("crypto", { getRandomValues: randomValues, randomUUID });
}

/** An insecure context: getRandomValues only (no randomUUID, no SubtleCrypto). */
export function withRandomValuesOnly(): void {
  vi.stubGlobal("crypto", { getRandomValues: randomValues });
}

/** The bytes given to saveFile, as text (BOM kept). */
export function savedText(bytes: ArrayBuffer): string {
  return new TextDecoder("utf-8", { ignoreBOM: true }).decode(bytes);
}
