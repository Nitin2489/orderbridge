import { DESK_PATH } from "@/features/desk/paths";
import { isOrderId } from "@/features/orders/paths";

export const EXPORTS_PATH = `${DESK_PATH}/exports`;

export function exportBatchPath(batchId: string): string {
  return `${EXPORTS_PATH}/${encodeURIComponent(batchId)}`;
}

/** Batch ids are UUIDs, like order ids; anything else is not worth a request. */
export function isBatchId(value: string): boolean {
  return isOrderId(value);
}

/**
 * The CSV download path relative to the API base (/api/v1), built from the
 * batch id. The page never follows a URL taken from a response body.
 */
export function batchCsvApiPath(batchId: string): string {
  return `/desk/exports/${encodeURIComponent(batchId)}/csv`;
}

/** "orderbridge-tally-EX-2026-000001.csv", as the backend names it. */
export function csvFilename(batchNumber: string): string {
  const safe = batchNumber.replace(/[^A-Za-z0-9-]/g, "");
  return `orderbridge-tally-${safe || "export"}.csv`;
}
