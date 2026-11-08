import { retailerOrderDetailSchema, type RetailerOrderDetail } from "@/features/orders/types";
import { api } from "@/lib/api";

import type { SubmitOrderPayload } from "./types";

/**
 * POST /api/v1/retailer/orders through the shared same-origin API client.
 *
 * The body carries only product ids, quantities, optional notes and the
 * idempotency key. The backend prices the order, derives the retailer from
 * the session and answers 201 (created) or 200 (the order already created
 * for this key and payload); both return the order, handled identically.
 */
export const SUBMIT_ORDER_MUTATION_KEY = ["retailer", "orders", "submit"] as const;

export async function submitOrder(payload: SubmitOrderPayload): Promise<RetailerOrderDetail> {
  const data = await api.post<unknown>("/retailer/orders", payload);
  const result = retailerOrderDetailSchema.safeParse(data);
  if (!result.success) {
    throw new Error("Unexpected response from the server.");
  }
  return result.data;
}
