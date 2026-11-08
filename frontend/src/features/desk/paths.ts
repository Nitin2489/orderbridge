export const DESK_PATH = "/desk";

export function deskOrderPath(orderId: string): string {
  return `${DESK_PATH}/orders/${encodeURIComponent(orderId)}`;
}
