import type { DeskOrderDetail } from "./types";

/**
 * Which desk actions an order allows, mirroring the backend's rules:
 * - preview and confirm: pending_review orders of an active retailer;
 * - reject: pending_review and on_hold orders (any retailer);
 * - on_hold orders cannot be confirmed (no endpoint releases a hold yet);
 * - confirmed, exported, rejected and draft orders are view-only.
 * The backend enforces all of this; these only decide what to offer.
 */
export interface AllowedActions {
  confirm: boolean;
  reject: boolean;
}

export function allowedActions(order: DeskOrderDetail): AllowedActions {
  return {
    confirm: order.status === "pending_review" && order.retailer.is_active,
    reject: order.status === "pending_review" || order.status === "on_hold",
  };
}
