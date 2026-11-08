export const ORDERS_PATH = "/retailer/orders";

export function orderPath(orderId: string): string {
  return `${ORDERS_PATH}/${encodeURIComponent(orderId)}`;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Order ids are UUIDs; anything else is not worth a request. */
export function isOrderId(value: string): boolean {
  return UUID.test(value);
}
