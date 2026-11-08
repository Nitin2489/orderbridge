/**
 * The request body of POST /api/v1/retailer/orders. Order response shapes
 * live in @/features/orders/types.
 */

/** Only ids, quantities, notes and the idempotency key. */
export interface SubmitOrderPayload {
  client_request_id: string;
  items: { product_id: string; quantity: number }[];
  notes: string | null;
}
