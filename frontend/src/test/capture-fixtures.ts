import type { Candidate, CaptureDetail, CaptureLine, CaptureSummary } from "@/features/captures/types";

/**
 * Capture API shapes for tests only, mirroring what the backend returns for
 * the client's demonstration message with the seeded data (biscuit 60,
 * detergent 85, tea 85; tea has no stock).
 */

export const CAPTURE_ID = "77777777-7777-4777-8777-777777777777";
export const SHARMA_ID = "7a1b2c3d-4e5f-4a6b-8c7d-222222222222";
export const DEMO =
  "bhaiya 5 peti biscuit chhota, 2 peti washing powder 1 kilo, chai patti 250 wala 1 peti";

export const BISCUIT = {
  product_id: "11111111-1111-4111-8111-111111111111",
  sku: "SKU-BS-075",
  name: "Glucose Biscuit 75g",
  stock_cases: 340,
};
export const DETERGENT = {
  product_id: "22222222-2222-4222-8222-222222222222",
  sku: "SKU-DT-1KG",
  name: "Detergent Powder 1kg",
  stock_cases: 85,
};
export const TEA = {
  product_id: "33333333-3333-4333-8333-333333333333",
  sku: "SKU-TE-250",
  name: "Tea 250g",
  stock_cases: 0,
};

export function candidate(
  product: typeof BISCUIT,
  score: number,
  reasons: string[] = [],
): Candidate {
  const band = score >= 85 ? "strong" : score >= 60 ? "possible" : score >= 30 ? "weak" : "very_weak";
  return { ...product, match_score: score, band, weak_match: score < 60, reasons };
}

export function makeLine(overrides: Partial<CaptureLine> = {}): CaptureLine {
  return {
    line_number: 1,
    start: 7,
    end: 28,
    original_text: "5 peti biscuit chhota",
    product_text: "biscuit chhota",
    product_start: 14,
    product_end: 28,
    quantity_text: "5",
    suggested_quantity: 5,
    unit_text: "peti",
    unit_hint: "case_word",
    notes: [],
    match: {
      proposal: candidate(BISCUIT, 60, ["Recognised 'biscuit' as biscuit.", "Pack size not stated."]),
      alternatives: [candidate(DETERGENT, 0), candidate(TEA, 0)],
      reasons: ["'chhota' does not identify a pack size."],
    },
    unit: { confirmed: false, unit: null, quantity: null, quantity_cases: null },
    resolution: "pending",
    selected_product: null,
    exclusion_reason: null,
    is_corrected: false,
    remember_alias: false,
    replace_existing_alias: false,
    ...overrides,
  };
}

export function demoLines(): CaptureLine[] {
  return [
    makeLine(),
    makeLine({
      line_number: 2,
      start: 30,
      end: 58,
      original_text: "2 peti washing powder 1 kilo",
      product_text: "washing powder 1 kilo",
      product_start: 37,
      product_end: 58,
      quantity_text: "2",
      suggested_quantity: 2,
      match: {
        proposal: candidate(DETERGENT, 85, ["Recognised 'washing powder' as detergent."]),
        alternatives: [candidate(BISCUIT, 0), candidate(TEA, 0)],
        reasons: [],
      },
    }),
    makeLine({
      line_number: 3,
      start: 60,
      end: 86,
      original_text: "chai patti 250 wala 1 peti",
      product_text: "chai patti 250 wala",
      product_start: 60,
      product_end: 79,
      quantity_text: "1",
      suggested_quantity: 1,
      match: {
        proposal: candidate(TEA, 85, ["Recognised 'chai patti' as tea."]),
        alternatives: [candidate(BISCUIT, 0), candidate(DETERGENT, 0)],
        reasons: [],
      },
    }),
  ];
}

export function makeCapture(overrides: Partial<CaptureDetail> = {}): CaptureDetail {
  const lines = overrides.lines ?? demoLines();
  return {
    id: CAPTURE_ID,
    version: 1,
    status: "needs_review",
    channel: "text",
    interpretation: {
      name: "rules@1",
      kind: "rules",
      is_ai_model: false,
      description: "Rule-based interpretation. Not an AI model.",
    },
    retailer: { id: SHARMA_ID, business_name: "Sharma General Store" },
    original_message: DEMO,
    ignored: [{ start: 0, end: 6, text: "bhaiya", reason: "greeting" }],
    created_at: "2026-11-08T05:00:00Z",
    submitted_at: null,
    discarded_at: null,
    discard_reason: null,
    order: null,
    can_submit: false,
    unresolved_lines: lines.filter((line) => line.resolution === "pending").map((l) => l.line_number),
    ...overrides,
    lines,
  };
}

export function makeSummary(overrides: Partial<CaptureSummary> = {}): CaptureSummary {
  return {
    id: CAPTURE_ID,
    version: 1,
    status: "needs_review",
    retailer: { id: SHARMA_ID, business_name: "Sharma General Store" },
    message_preview: DEMO,
    line_count: 3,
    unresolved_count: 3,
    created_at: "2026-11-08T05:00:00Z",
    order: null,
    ...overrides,
  };
}
