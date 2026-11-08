import { z } from "zod";

import { orderLineSchema } from "@/features/orders/types";

/**
 * Shapes of /api/v1/desk/orders, mirroring backend/app/schemas/desk.py.
 * Parsed at runtime. Money arrives as decimal strings and is only
 * formatted: every total, discount and credit figure comes from the backend.
 */

const decimalString = z.string().regex(/^-?\d+(\.\d+)?$/, "Expected a decimal string");

export const deskOrderSummarySchema = z.object({
  id: z.string(),
  order_number: z.string(),
  status: z.string(),
  source: z.string(),
  retailer_id: z.string(),
  retailer_business_name: z.string(),
  item_count: z.number().int(),
  subtotal: decimalString,
  discount_total: decimalString,
  grand_total: decimalString,
  tax_treatment: z.literal("exclusive"),
  created_at: z.string(),
  confirmed_at: z.string().nullable(),
});

export const deskOrderPageSchema = z.object({
  items: z.array(deskOrderSummarySchema),
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
});

export const retailerCreditSchema = z.object({
  retailer_id: z.string(),
  business_name: z.string(),
  is_active: z.boolean(),
  credit_limit: decimalString,
  outstanding_balance: decimalString,
  available_credit: decimalString,
});

export const orderEventSchema = z.object({
  id: z.string(),
  event_type: z.string(),
  from_status: z.string().nullable(),
  to_status: z.string().nullable(),
  actor_user_id: z.string().nullable(),
  actor_name: z.string().nullable(),
  reason: z.string().nullable(),
  details: z.record(z.string(), z.unknown()).nullable(),
  created_at: z.string(),
});

export const deskOrderDetailSchema = z.object({
  id: z.string(),
  order_number: z.string(),
  status: z.string(),
  source: z.string(),
  subtotal: decimalString,
  discount_total: decimalString,
  grand_total: decimalString,
  tax_treatment: z.literal("exclusive"),
  notes: z.string().nullable(),
  created_at: z.string(),
  confirmed_at: z.string().nullable(),
  confirmed_by_user_id: z.string().nullable(),
  retailer: retailerCreditSchema,
  items: z.array(orderLineSchema),
  events: z.array(orderEventSchema),
});

export const previewLineSchema = z.object({
  line_number: z.number().int(),
  order_item_id: z.string(),
  product_id: z.string(),
  sku: z.string(),
  product_name: z.string(),
  quantity: z.number().int(),
  stock_required: z.number().int(),
  stored_unit_price: decimalString,
  current_unit_price: decimalString,
  stored_free_quantity: z.number().int(),
  current_free_quantity: z.number().int(),
  stored_discount_amount: decimalString,
  current_discount_amount: decimalString,
  stored_line_total: decimalString,
  current_line_total: decimalString,
  stored_scheme_name: z.string().nullable(),
  current_scheme_name: z.string().nullable(),
  current_subtotal: decimalString,
  price_changed: z.boolean(),
});

export const lineIssueSchema = z.object({
  line_number: z.number().int(),
  order_item_id: z.string(),
  product_id: z.string(),
  sku: z.string(),
  code: z.string(),
  message: z.string(),
});

export const deskOrderPreviewSchema = z.object({
  order_id: z.string(),
  lines: z.array(previewLineSchema),
  issues: z.array(lineIssueSchema),
  has_price_changes: z.boolean(),
  subtotal: decimalString,
  discount_total: decimalString,
  grand_total: decimalString,
  totals_cover_all_lines: z.boolean(),
  available_credit: decimalString,
  exceeds_credit_limit: z.boolean(),
  can_confirm: z.boolean(),
  fingerprint: z.string().nullable(),
  tax_treatment: z.literal("exclusive"),
});

export const CONFIRMATION_OUTCOMES = [
  "confirmed",
  "on_hold_credit_limit",
  "already_confirmed",
] as const;

export const confirmOrderResponseSchema = z.object({
  outcome: z.enum(CONFIRMATION_OUTCOMES),
  required_credit: decimalString.nullable().optional(),
  available_credit: decimalString.nullable().optional(),
  order: deskOrderDetailSchema,
});

export const rejectOrderResponseSchema = z.object({
  already_rejected: z.boolean(),
  rejection: orderEventSchema.nullable(),
  order: deskOrderDetailSchema,
});

/** 409 from confirm: the figures changed; nothing was done. */
export const previewOutdatedSchema = z.object({
  detail: z.string(),
  preview: deskOrderPreviewSchema,
});

/** 422 from confirm: lines that cannot be confirmed; nothing was done. */
export const orderIssuesSchema = z.object({
  detail: z.string(),
  issues: z.array(lineIssueSchema),
});

export type DeskOrderSummary = z.infer<typeof deskOrderSummarySchema>;
export type DeskOrderPage = z.infer<typeof deskOrderPageSchema>;
export type RetailerCredit = z.infer<typeof retailerCreditSchema>;
export type OrderEvent = z.infer<typeof orderEventSchema>;
export type DeskOrderDetail = z.infer<typeof deskOrderDetailSchema>;
export type PreviewLine = z.infer<typeof previewLineSchema>;
export type LineIssue = z.infer<typeof lineIssueSchema>;
export type DeskOrderPreview = z.infer<typeof deskOrderPreviewSchema>;
export type ConfirmOrderResponse = z.infer<typeof confirmOrderResponseSchema>;
export type RejectOrderResponse = z.infer<typeof rejectOrderResponseSchema>;
