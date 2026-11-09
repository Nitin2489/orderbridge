import { z } from "zod";

/**
 * Shapes of /api/v1/captures, mirroring backend/app/schemas/captures.py.
 * Parsed at runtime. Match scores are deterministic heuristic ranking
 * scores (0-100), never probabilities. Captures carry no prices: the order
 * created on submission is priced by the backend.
 */

export const CAPTURE_STATUSES = [
  "needs_review",
  "ready",
  "submitted",
  "discarded",
  "failed",
] as const;
export type CaptureStatus = (typeof CAPTURE_STATUSES)[number];

export const candidateSchema = z.object({
  product_id: z.string(),
  sku: z.string(),
  name: z.string(),
  match_score: z.number().int().min(0).max(100),
  band: z.enum(["strong", "possible", "weak", "very_weak"]),
  weak_match: z.boolean(),
  reasons: z.array(z.string()),
  /** Current stock in cases, for display; checked again on submission. */
  stock_cases: z.number().int().nullable().optional(),
});

export const captureLineSchema = z.object({
  line_number: z.number().int(),
  start: z.number().int(),
  end: z.number().int(),
  original_text: z.string(),
  product_text: z.string().nullable(),
  product_start: z.number().int().nullable(),
  product_end: z.number().int().nullable(),
  quantity_text: z.string().nullable(),
  suggested_quantity: z.number().int().nullable(),
  unit_text: z.string().nullable(),
  unit_hint: z.enum(["case_word", "piece_word", "none"]),
  notes: z.array(z.string()),
  match: z.object({
    proposal: candidateSchema.nullable(),
    alternatives: z.array(candidateSchema),
    reasons: z.array(z.string()),
  }),
  unit: z.object({
    confirmed: z.boolean(),
    unit: z.enum(["case", "piece"]).nullable(),
    quantity: z.number().int().nullable(),
    quantity_cases: z.number().int().nullable(),
  }),
  resolution: z.enum(["pending", "included", "excluded"]),
  selected_product: z
    .object({
      id: z.string(),
      sku: z.string(),
      name: z.string(),
      units_per_case: z.number().int(),
    })
    .nullable(),
  exclusion_reason: z.string().nullable(),
  is_corrected: z.boolean(),
  remember_alias: z.boolean(),
  replace_existing_alias: z.boolean(),
});

const orderLinkSchema = z.object({
  id: z.string(),
  order_number: z.string(),
  status: z.string(),
});

export const captureDetailSchema = z.object({
  id: z.string(),
  version: z.number().int(),
  status: z.enum(CAPTURE_STATUSES),
  channel: z.string(),
  interpretation: z.object({
    name: z.string(),
    kind: z.string(),
    is_ai_model: z.boolean(),
    description: z.string(),
    fallback_note: z.string().nullable().optional(),
  }),
  retailer: z.object({ id: z.string(), business_name: z.string() }),
  original_message: z.string(),
  ignored: z.array(
    z.object({
      start: z.number().int(),
      end: z.number().int(),
      text: z.string(),
      reason: z.enum(["greeting", "no_words"]),
    }),
  ),
  created_at: z.string(),
  submitted_at: z.string().nullable(),
  discarded_at: z.string().nullable(),
  discard_reason: z.string().nullable(),
  order: orderLinkSchema.nullable(),
  can_submit: z.boolean(),
  unresolved_lines: z.array(z.number().int()),
  lines: z.array(captureLineSchema),
});

export const captureSummarySchema = z.object({
  id: z.string(),
  version: z.number().int(),
  status: z.enum(CAPTURE_STATUSES),
  retailer: z.object({ id: z.string(), business_name: z.string() }),
  message_preview: z.string(),
  line_count: z.number().int(),
  unresolved_count: z.number().int(),
  created_at: z.string(),
  order: orderLinkSchema.nullable(),
});

export const capturePageSchema = z.object({
  items: z.array(captureSummarySchema),
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
});

export const submitCaptureResponseSchema = z.object({
  created: z.boolean(),
  order: orderLinkSchema,
  capture: captureDetailSchema,
});

export const retailerDirectorySchema = z.object({
  items: z.array(
    z.object({ id: z.string(), business_name: z.string(), is_active: z.boolean() }),
  ),
});

// Error bodies
export const staleCaptureSchema = z.object({
  detail: z.string(),
  current_version: z.number().int(),
});
export const notReadySchema = z.object({
  detail: z.string(),
  unresolved_lines: z.array(z.number().int()),
});
export const linesInvalidSchema = z.object({
  detail: z.string(),
  issues: z.array(
    z.object({
      line_number: z.number().int(),
      product_id: z.string(),
      code: z.string(),
      message: z.string(),
    }),
  ),
});
export const aliasConflictSchema = z.object({
  detail: z.string(),
  line_number: z.number().int(),
  alias_text: z.string(),
  current_product_id: z.string(),
});

/** 409 on create (order desk): an equivalent message was captured recently. */
export const possibleDuplicateSchema = z.object({
  detail: z.string(),
  code: z.literal("possible_duplicate"),
  duplicates: z.array(
    z.object({
      capture_id: z.string(),
      created_at: z.string(),
      status: z.enum(CAPTURE_STATUSES),
      order: z
        .object({ id: z.string(), order_number: z.string(), status: z.string() })
        .nullable(),
    }),
  ),
});
export type PossibleDuplicate = z.infer<typeof possibleDuplicateSchema>["duplicates"][number];

export type Candidate = z.infer<typeof candidateSchema>;
export type CaptureLine = z.infer<typeof captureLineSchema>;
export type CaptureDetail = z.infer<typeof captureDetailSchema>;
export type CaptureSummary = z.infer<typeof captureSummarySchema>;
export type CapturePage = z.infer<typeof capturePageSchema>;
export type SubmitCaptureResponse = z.infer<typeof submitCaptureResponseSchema>;
export type RetailerDirectory = z.infer<typeof retailerDirectorySchema>;
export type LineIssue = z.infer<typeof linesInvalidSchema>["issues"][number];

/** A review decision for one line (POST /captures/{id}/lines/{n}). */
export type LineDecision =
  | { action: "confirm_unit"; unit: "case" | "piece"; quantity: number }
  | { action: "clear_unit" }
  | {
      action: "include";
      product_id: string;
      remember_alias?: boolean;
      replace_existing_alias?: boolean;
    }
  | { action: "exclude"; reason: string }
  | { action: "reset" };
