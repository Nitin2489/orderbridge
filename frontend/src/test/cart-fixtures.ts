import {
  retailerOrderSummarySchema,
  type RetailerOrderDetail,
  type RetailerOrderSummary,
} from "@/features/orders/types";
import type { CatalogueProduct } from "@/features/catalogue/types";

/** Catalogue products shaped like the backend's, for tests only. */
export function makeProduct(overrides: Partial<CatalogueProduct> = {}): CatalogueProduct {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    sku: "SKU-BS-075",
    name: "Glucose biscuit 75 g",
    description: null,
    brand: "Sample Foods",
    category: "Biscuits",
    unit_name: "case",
    units_per_case: 96,
    stock_quantity: 340,
    is_available: true,
    unit_price: "777.60",
    tax_treatment: "exclusive",
    scheme_status: "none",
    scheme: null,
    ...overrides,
  };
}

export const biscuits = makeProduct();

export const detergent = makeProduct({
  id: "22222222-2222-4222-8222-222222222222",
  sku: "SKU-DT-1KG",
  name: "Detergent powder 1 kg",
  units_per_case: 20,
  stock_quantity: 85,
  unit_price: "2030.00",
});

export const tea = makeProduct({
  id: "33333333-3333-4333-8333-333333333333",
  sku: "SKU-TE-250",
  name: "Tea leaf 250 g",
  stock_quantity: 0,
  is_available: false,
});

/** A server response for a submitted order (RetailerOrderDetail). */
export function makeOrder(overrides: Partial<RetailerOrderDetail> = {}): RetailerOrderDetail {
  return {
    id: "44444444-4444-4444-8444-444444444444",
    order_number: "OB-2026-000123",
    status: "pending_review",
    source: "retailer_app",
    item_count: 1,
    subtotal: "7776.00",
    discount_total: "0.00",
    grand_total: "7776.00",
    tax_treatment: "exclusive",
    created_at: "2026-11-07T10:00:00Z",
    confirmed_at: null,
    notes: null,
    items: [
      {
        line_number: 1,
        product_id: biscuits.id,
        sku: biscuits.sku,
        product_name: biscuits.name,
        quantity: 10,
        free_quantity: 1,
        unit_price: "777.60",
        discount_amount: "0.00",
        line_total: "7776.00",
        scheme_name: "Buy 10 get 1 free",
      },
    ],
    ...overrides,
  };
}

/** A RetailerOrderSummary (one order-history row), for tests only. */
export function makeOrderSummary(
  overrides: Partial<RetailerOrderSummary> = {},
): RetailerOrderSummary {
  // The schema drops the detail-only fields (items, notes).
  return { ...retailerOrderSummarySchema.parse(makeOrder()), ...overrides };
}

/** A page of RetailerOrderPage. */
export function makeOrderPage(
  items: RetailerOrderSummary[],
  { total = items.length, offset = 0, limit = 20 } = {},
) {
  return { items, total, limit, offset };
}
