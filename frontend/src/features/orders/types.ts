import { z } from "zod";

/**
 * Shapes of /api/v1/retailer/orders responses, mirroring
 * backend/app/schemas/orders.py. Parsed at runtime.
 *
 * Money arrives as decimal strings and is only formatted, never computed.
 * `status` and `source` are kept as strings so an unexpected value is shown
 * rather than breaking the screen (see status.ts).
 */

const decimalString = z.string().regex(/^-?\d+(\.\d+)?$/, "Expected a decimal string");

export const orderLineSchema = z.object({
  line_number: z.number().int(),
  product_id: z.string(),
  sku: z.string(),
  product_name: z.string(),
  quantity: z.number().int(),
  free_quantity: z.number().int(),
  unit_price: decimalString,
  discount_amount: decimalString,
  line_total: decimalString,
  scheme_name: z.string().nullable(),
});

/** RetailerOrderSummary: one row of the order history. */
export const retailerOrderSummarySchema = z.object({
  id: z.string(),
  order_number: z.string(),
  status: z.string(),
  source: z.string(),
  item_count: z.number().int(),
  subtotal: decimalString,
  discount_total: decimalString,
  grand_total: decimalString,
  tax_treatment: z.literal("exclusive"),
  created_at: z.string(),
  confirmed_at: z.string().nullable(),
});

/** RetailerOrderDetail: one order with its lines (also returned on submit/repeat). */
export const retailerOrderDetailSchema = retailerOrderSummarySchema.extend({
  notes: z.string().nullable(),
  items: z.array(orderLineSchema),
  /** The order desk's reason, only on a rejected order. */
  rejection_reason: z.string().nullable().optional(),
});

/** RetailerOrderPage: GET /retailer/orders. */
export const retailerOrderPageSchema = z.object({
  items: z.array(retailerOrderSummarySchema),
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
});

/** One issue from a 422 OrderLinesErrorResponse (nothing was created). */
export const orderLineIssueSchema = z.object({
  line_number: z.number().int(),
  product_id: z.string(),
  code: z.string(),
  message: z.string(),
});

export const orderLinesErrorSchema = z.object({
  detail: z.string(),
  issues: z.array(orderLineIssueSchema),
});

export type OrderLine = z.infer<typeof orderLineSchema>;
export type RetailerOrderSummary = z.infer<typeof retailerOrderSummarySchema>;
export type RetailerOrderDetail = z.infer<typeof retailerOrderDetailSchema>;
export type RetailerOrderPage = z.infer<typeof retailerOrderPageSchema>;
export type OrderLineIssue = z.infer<typeof orderLineIssueSchema>;
