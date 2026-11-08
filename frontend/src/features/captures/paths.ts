import { DESK_PATH } from "@/features/desk/paths";
import { isOrderId } from "@/features/orders/paths";

export type CaptureRole = "retailer" | "order_desk";

/** Where each role's capture screens live. */
export const CAPTURE_BASE: Record<CaptureRole, string> = {
  retailer: "/retailer/capture",
  order_desk: `${DESK_PATH}/captures`,
};

export function capturesPath(role: CaptureRole): string {
  return CAPTURE_BASE[role];
}

export function newCapturePath(role: CaptureRole): string {
  return `${CAPTURE_BASE[role]}/new`;
}

export function capturePath(role: CaptureRole, captureId: string): string {
  return `${CAPTURE_BASE[role]}/${encodeURIComponent(captureId)}`;
}

/** Capture ids are UUIDs; anything else is not worth a request. */
export function isCaptureId(value: string): boolean {
  return isOrderId(value);
}
