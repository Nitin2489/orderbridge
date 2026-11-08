import { z } from "zod";

import { parseOffsetTimestamp } from "./format";

/**
 * Response shapes of /api/v1/retailer/catalogue, mirroring
 * backend/app/schemas/catalogue.py. Parsed at runtime so a contract change
 * fails at the boundary.
 *
 * Money arrives as decimal strings (e.g. "777.60") and stays a string: the
 * frontend formats it for display but never does arithmetic with it.
 */

const decimalString = z.string().regex(/^-?\d+(\.\d+)?$/, "Expected a decimal string");
const offsetTimestamp = z
  .string()
  .refine(
    (value) => parseOffsetTimestamp(value) !== null,
    "Expected a valid ISO timestamp with a time zone offset",
  );

export const SCHEME_TYPES = ["free_goods", "slab_discount"] as const;
export const SCHEME_STATUSES = ["none", "active", "configuration_error"] as const;

export const schemeReferenceSchema = z.object({
  quantity: z.number().int(),
  free_quantity: z.number().int(),
  subtotal: decimalString,
  discount_amount: decimalString,
  illustrative_total: decimalString,
});

export const catalogueSchemeSchema = z.object({
  id: z.string(),
  name: z.string(),
  scheme_type: z.enum(SCHEME_TYPES),
  minimum_quantity: z.number().int(),
  free_quantity: z.number().int(),
  discount_percent: decimalString,
  starts_at: offsetTimestamp,
  ends_at: offsetTimestamp.nullable(),
  reference: schemeReferenceSchema.nullable(),
});

export const catalogueProductSchema = z.object({
  id: z.string(),
  sku: z.string(),
  name: z.string(),
  description: z.string().nullable(),
  brand: z.string(),
  category: z.string(),
  unit_name: z.string(),
  units_per_case: z.number().int(),
  stock_quantity: z.number().int(),
  is_available: z.boolean(),
  unit_price: decimalString,
  tax_treatment: z.literal("exclusive"),
  scheme_status: z.enum(SCHEME_STATUSES),
  scheme: catalogueSchemeSchema.nullable(),
});

export const cataloguePageSchema = z.object({
  items: z.array(catalogueProductSchema),
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
});

export const catalogueCategoriesSchema = z.object({
  categories: z.array(z.string()),
});

export type SchemeReference = z.infer<typeof schemeReferenceSchema>;
export type CatalogueScheme = z.infer<typeof catalogueSchemeSchema>;
export type CatalogueProduct = z.infer<typeof catalogueProductSchema>;
export type CataloguePage = z.infer<typeof cataloguePageSchema>;
