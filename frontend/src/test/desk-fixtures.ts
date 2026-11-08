import type {
  DeskOrderDetail,
  DeskOrderPreview,
  DeskOrderSummary,
  OrderEvent,
  PreviewLine,
} from "@/features/desk/types";

/** Desk API shapes for tests only. All money is backend-style decimal strings. */

export const ORDER_ID = "44444444-4444-4444-8444-444444444444";
export const RETAILER_ID = "7a1b2c3d-4e5f-4a6b-8c7d-222222222222";
export const FINGERPRINT = "a".repeat(64);
export const NEW_FINGERPRINT = "b".repeat(64);

export function makeDeskSummary(overrides: Partial<DeskOrderSummary> = {}): DeskOrderSummary {
  return {
    id: ORDER_ID,
    order_number: "OB-2026-000123",
    status: "pending_review",
    source: "retailer_app",
    retailer_id: RETAILER_ID,
    retailer_business_name: "Sharma General Store",
    item_count: 1,
    subtotal: "7776.00",
    discount_total: "0.00",
    grand_total: "7776.00",
    tax_treatment: "exclusive",
    created_at: "2026-11-07T10:00:00Z",
    confirmed_at: null,
    ...overrides,
  };
}

export function makeEvent(overrides: Partial<OrderEvent> = {}): OrderEvent {
  return {
    id: "e1111111-1111-4111-8111-111111111111",
    event_type: "submitted",
    from_status: null,
    to_status: "pending_review",
    actor_user_id: null,
    actor_name: "Amit Sharma",
    reason: null,
    details: null,
    created_at: "2026-11-07T10:00:00Z",
    ...overrides,
  };
}

export function makeDeskDetail(overrides: Partial<DeskOrderDetail> = {}): DeskOrderDetail {
  return {
    id: ORDER_ID,
    order_number: "OB-2026-000123",
    status: "pending_review",
    source: "retailer_app",
    subtotal: "7776.00",
    discount_total: "0.00",
    grand_total: "7776.00",
    tax_treatment: "exclusive",
    notes: "Deliver before noon",
    created_at: "2026-11-07T10:00:00Z",
    confirmed_at: null,
    confirmed_by_user_id: null,
    retailer: {
      retailer_id: RETAILER_ID,
      business_name: "Sharma General Store",
      is_active: true,
      credit_limit: "50000.00",
      outstanding_balance: "10000.00",
      available_credit: "40000.00",
    },
    items: [
      {
        line_number: 1,
        product_id: "11111111-1111-4111-8111-111111111111",
        sku: "SKU-BS-075",
        product_name: "Glucose biscuit 75 g",
        quantity: 10,
        free_quantity: 1,
        unit_price: "777.60",
        discount_amount: "0.00",
        line_total: "7776.00",
        scheme_name: "Buy 10 get 1 free",
      },
    ],
    events: [makeEvent()],
    ...overrides,
  };
}

export function makePreviewLine(overrides: Partial<PreviewLine> = {}): PreviewLine {
  return {
    line_number: 1,
    order_item_id: "d1111111-1111-4111-8111-111111111111",
    product_id: "11111111-1111-4111-8111-111111111111",
    sku: "SKU-BS-075",
    product_name: "Glucose biscuit 75 g",
    quantity: 10,
    stock_required: 11,
    stored_unit_price: "777.60",
    current_unit_price: "777.60",
    stored_free_quantity: 1,
    current_free_quantity: 1,
    stored_discount_amount: "0.00",
    current_discount_amount: "0.00",
    stored_line_total: "7776.00",
    current_line_total: "7776.00",
    stored_scheme_name: "Buy 10 get 1 free",
    current_scheme_name: "Buy 10 get 1 free",
    current_subtotal: "7776.00",
    price_changed: false,
    ...overrides,
  };
}

export function makePreview(overrides: Partial<DeskOrderPreview> = {}): DeskOrderPreview {
  return {
    order_id: ORDER_ID,
    lines: [makePreviewLine()],
    issues: [],
    has_price_changes: false,
    subtotal: "7776.00",
    discount_total: "0.00",
    grand_total: "7776.00",
    totals_cover_all_lines: true,
    available_credit: "40000.00",
    exceeds_credit_limit: false,
    can_confirm: true,
    fingerprint: FINGERPRINT,
    tax_treatment: "exclusive",
    ...overrides,
  };
}
