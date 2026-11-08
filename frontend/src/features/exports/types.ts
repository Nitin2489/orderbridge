import { z } from "zod";

/**
 * Shapes of /api/v1/desk/exports, mirroring backend/app/schemas/exports.py.
 * Parsed at runtime. Money arrives as decimal strings and is only
 * formatted: eligibility, batch contents, counts and totals all come from
 * the backend.
 */

const decimalString = z.string().regex(/^-?\d+(\.\d+)?$/, "Expected a decimal string");
const sha256Hex = z.string().regex(/^[0-9a-f]{64}$/i, "Expected a SHA-256 hex digest");

export const eligibleExportSchema = z.object({
  order_count: z.number().int().nonnegative(),
  net_total: decimalString,
});

export const exportBatchSummarySchema = z.object({
  id: z.string(),
  batch_number: z.string(),
  created_at: z.string(),
  created_by_name: z.string().nullable(),
  order_count: z.number().int().nonnegative(),
  net_total: decimalString,
  csv_sha256: sha256Hex,
  csv_url: z.string(),
});

export const exportBatchPageSchema = z.object({
  items: z.array(exportBatchSummarySchema),
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
});

export const exportedOrderSchema = z.object({
  id: z.string(),
  order_number: z.string(),
  status: z.string(),
  retailer_business_name: z.string(),
  confirmed_at: z.string().nullable(),
  net_total: decimalString,
});

export const exportBatchDetailSchema = exportBatchSummarySchema.extend({
  orders: z.array(exportedOrderSchema),
});

/** 409 when explicitly requested orders are not eligible (not sent by this UI). */
export const ineligibleOrdersSchema = z.object({
  detail: z.string(),
  ineligible: z.array(z.object({ order_id: z.string(), reason: z.string() })),
});

export type EligibleExport = z.infer<typeof eligibleExportSchema>;
export type ExportBatchSummary = z.infer<typeof exportBatchSummarySchema>;
export type ExportBatchPage = z.infer<typeof exportBatchPageSchema>;
export type ExportedOrder = z.infer<typeof exportedOrderSchema>;
export type ExportBatchDetail = z.infer<typeof exportBatchDetailSchema>;

/** What a successful POST /desk/exports returned. */
export interface CreateExportResult {
  batch: ExportBatchSummary;
  /** true for 201 (a new batch); false for 200 (an identical earlier request's batch). */
  created: boolean;
}

/** The fields a CSV download needs. */
export type DownloadableBatch = Pick<ExportBatchSummary, "id" | "batch_number" | "csv_sha256">;
