import type { CatalogueProduct } from "@/features/catalogue/types";
import type { RetailerOrderDetail } from "@/features/orders/types";

import type { SubmitOrderPayload } from "./types";

/**
 * Cart state and its pure reducer (no React), so the rules are easy to test.
 *
 * Quantities are counts of the product's `unit_name` (a case for the seeded
 * products): the backend prices `unit_price` (per unit_name) x quantity and
 * checks quantity plus free goods against `stock_quantity` in the same unit.
 */

/** Backend limits (backend/app/schemas/orders.py, retailer_order_service.py). */
export const MIN_LINE_QUANTITY = 1;
export const MAX_LINE_QUANTITY = 10_000;
export const MAX_ORDER_LINES = 50;
export const MAX_NOTES_LENGTH = 500;

export interface CartLine {
  /**
   * The catalogue product as it was when added: for display only (name,
   * SKU, unit, stock, scheme). The backend re-checks everything.
   */
  product: CatalogueProduct;
  quantity: number;
}

/** A submission whose outcome is not final, kept to retry with the same key. */
export interface SubmissionAttempt {
  clientRequestId: string;
  /** The exact payload (without the key) the key was used for. */
  signature: string;
}

export interface CartState {
  /** The retailer this cart belongs to; null when no retailer is signed in. */
  ownerId: string | null;
  lines: CartLine[];
  notes: string;
  attempt: SubmissionAttempt | null;
  /** The last successfully submitted order, shown as the confirmation. */
  confirmation: RetailerOrderDetail | null;
}

export type CartAction =
  | { type: "reset"; ownerId: string | null }
  | { type: "restore"; ownerId: string; lines: CartLine[]; notes: string }
  | { type: "add"; product: CatalogueProduct; quantity?: number }
  | { type: "setQuantity"; productId: string; quantity: number }
  | { type: "remove"; productId: string }
  | { type: "setNotes"; notes: string }
  | { type: "startAttempt"; attempt: SubmissionAttempt }
  | { type: "abandonAttempt" }
  | { type: "submitted"; order: RetailerOrderDetail; signature: string; ownerId: string }
  | { type: "dismissConfirmation" };

export function emptyCart(ownerId: string | null): CartState {
  return { ownerId, lines: [], notes: "", attempt: null, confirmation: null };
}

/**
 * Why a product cannot be added (a button label), or null when it can. The
 * card's stock badge already says "Out of stock", so this wording differs.
 */
export function unorderableReason(product: CatalogueProduct): string | null {
  if (!product.is_available || product.stock_quantity <= 0) {
    return "Not available to order";
  }
  if (product.scheme_status === "configuration_error") {
    return "Cannot be ordered right now";
  }
  return null;
}

export function clampQuantity(quantity: number): number {
  if (!Number.isFinite(quantity)) {
    return MIN_LINE_QUANTITY;
  }
  return Math.min(MAX_LINE_QUANTITY, Math.max(MIN_LINE_QUANTITY, Math.trunc(quantity)));
}

export function cartReducer(state: CartState, action: CartAction): CartState {
  switch (action.type) {
    case "reset":
      return emptyCart(action.ownerId);

    case "restore":
      // A saved cart only ever fills this retailer's still-empty cart: never
      // another retailer's, and never over products added meanwhile.
      if (state.ownerId !== action.ownerId || state.lines.length > 0 || state.notes !== "") {
        return state;
      }
      return {
        ...state,
        lines: action.lines
          .slice(0, MAX_ORDER_LINES)
          .map((line) => ({ product: line.product, quantity: clampQuantity(line.quantity) })),
        notes: action.notes.slice(0, MAX_NOTES_LENGTH),
      };

    case "add": {
      if (unorderableReason(action.product)) {
        return state;
      }
      const amount = action.quantity ?? 1;
      const existing = state.lines.find((line) => line.product.id === action.product.id);
      if (existing) {
        // One line per product: adding again increases its quantity.
        return {
          ...state,
          confirmation: null,
          lines: state.lines.map((line) =>
            line === existing
              ? { product: action.product, quantity: clampQuantity(line.quantity + amount) }
              : line,
          ),
        };
      }
      if (state.lines.length >= MAX_ORDER_LINES) {
        return state;
      }
      return {
        ...state,
        confirmation: null,
        lines: [...state.lines, { product: action.product, quantity: clampQuantity(amount) }],
      };
    }

    case "setQuantity":
      return {
        ...state,
        lines: state.lines.map((line) =>
          line.product.id === action.productId
            ? { ...line, quantity: clampQuantity(action.quantity) }
            : line,
        ),
      };

    case "remove":
      return {
        ...state,
        lines: state.lines.filter((line) => line.product.id !== action.productId),
      };

    case "setNotes":
      return { ...state, notes: action.notes.slice(0, MAX_NOTES_LENGTH) };

    case "startAttempt":
      return { ...state, attempt: action.attempt };

    case "abandonAttempt":
      return { ...state, attempt: null };

    case "submitted":
      // Another retailer's response (they signed out meanwhile): ignore it.
      if (state.ownerId !== action.ownerId) {
        return state;
      }
      // Clear only if the cart still holds exactly what was submitted (it is
      // locked while submitting, so this is the normal case).
      if (orderSignature(state) !== action.signature) {
        return { ...state, attempt: null, confirmation: action.order };
      }
      return { ...emptyCart(state.ownerId), confirmation: action.order };

    case "dismissConfirmation":
      return { ...state, confirmation: null };
  }
}

/** Notes as sent: trimmed, and null when empty (as the backend normalises). */
export function normalisedNotes(notes: string): string | null {
  const trimmed = notes.trim();
  return trimmed ? trimmed : null;
}

/** The payload without its key: lines in cart order, and notes. */
function payloadBody(state: Pick<CartState, "lines" | "notes">) {
  return {
    items: state.lines.map((line) => ({
      product_id: line.product.id,
      quantity: line.quantity,
    })),
    notes: normalisedNotes(state.notes),
  };
}

/** A stable string identifying exactly what would be submitted. */
export function orderSignature(state: Pick<CartState, "lines" | "notes">): string {
  return JSON.stringify(payloadBody(state));
}

/** The request body for a submission with the given idempotency key. */
export function buildPayload(
  state: Pick<CartState, "lines" | "notes">,
  clientRequestId: string,
): SubmitOrderPayload {
  return { client_request_id: clientRequestId, ...payloadBody(state) };
}

/**
 * The idempotency key for submitting the current cart: the pending
 * attempt's key when the payload is unchanged (a retry), otherwise a new one
 * (a new submission).
 */
export function keyForSubmission(
  state: CartState,
  newKey: () => string,
): SubmissionAttempt {
  const signature = orderSignature(state);
  if (state.attempt && state.attempt.signature === signature) {
    return state.attempt;
  }
  return { clientRequestId: newKey(), signature };
}
