import { formatIndiaDate } from "@/features/catalogue/format";

import type { RetailerOrderSummary } from "./types";

/**
 * The steps an order goes through inside OrderBridge, derived only from its
 * real status and timestamps. There are no delivery or dispatch steps: the
 * app does not track them (out of scope), so none are shown.
 *
 *   placed -> order desk review -> confirmed -> sent for billing (Tally)
 *
 * An order on hold or rejected stops at the order desk.
 */

export type StepState = "done" | "current" | "upcoming" | "held" | "stopped";

export interface ProgressStep {
  key: string;
  label: string;
  state: StepState;
  /** India date, when the API gives one for this step. */
  date: string | null;
}

type Order = Pick<RetailerOrderSummary, "status" | "created_at" | "confirmed_at">;

function step(
  key: string,
  label: string,
  state: StepState,
  timestamp: string | null = null,
): ProgressStep {
  return { key, label, state, date: timestamp ? formatIndiaDate(timestamp) : null };
}

/** The progress steps for an order, or null for a status with no flow. */
export function orderProgress(order: Order): ProgressStep[] | null {
  const placed = step("placed", "Placed", "done", order.created_at);
  switch (order.status) {
    case "pending_review":
      return [
        placed,
        step("review", "Order desk review", "current"),
        step("confirm", "Confirmation", "upcoming"),
        step("billing", "Sent for billing", "upcoming"),
      ];
    case "on_hold":
      return [
        placed,
        step("review", "On hold with the order desk", "held"),
        step("confirm", "Confirmation", "upcoming"),
        step("billing", "Sent for billing", "upcoming"),
      ];
    case "confirmed":
      return [
        placed,
        step("review", "Order desk review", "done"),
        step("confirm", "Confirmed by the order desk", "done", order.confirmed_at),
        step("billing", "Sent for billing", "upcoming"),
      ];
    case "exported":
      return [
        placed,
        step("review", "Order desk review", "done"),
        step("confirm", "Confirmed by the order desk", "done", order.confirmed_at),
        step("billing", "Sent for billing", "done"),
      ];
    case "rejected":
      return [placed, step("review", "Rejected by the order desk", "stopped")];
    default:
      return null;
  }
}
