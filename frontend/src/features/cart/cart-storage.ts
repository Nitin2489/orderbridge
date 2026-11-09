import { z } from "zod";

import { catalogueProductSchema, type CatalogueProduct } from "@/features/catalogue/types";
import { api } from "@/lib/api";

import {
  MAX_LINE_QUANTITY,
  MAX_NOTES_LENGTH,
  MAX_ORDER_LINES,
  MIN_LINE_QUANTITY,
  type CartLine,
} from "./cart-state";

/**
 * The retailer's cart saved in localStorage, so a reload or a discarded tab
 * does not lose it.
 *
 * One key per retailer, and the saved data names its owner, so one
 * retailer's cart is never restored for another. Only product ids,
 * quantities and notes are saved: no prices, stock, credit or account data.
 * Products are fetched again from the backend (the retailer's own catalogue)
 * when the cart is restored, so what is shown is current; the backend still
 * prices and checks everything at submission. Anything unreadable or not
 * matching this exact shape is deleted and ignored.
 */

export const CART_STORAGE_PREFIX = "orderbridge.cart.v1.";

export function cartStorageKey(ownerId: string): string {
  return `${CART_STORAGE_PREFIX}${ownerId}`;
}

const savedCartSchema = z
  .object({
    version: z.literal(1),
    ownerId: z.string().min(1),
    lines: z
      .array(
        z
          .object({
            productId: z.string().uuid(),
            quantity: z.number().int().min(MIN_LINE_QUANTITY).max(MAX_LINE_QUANTITY),
          })
          .strict(),
      )
      .max(MAX_ORDER_LINES),
    notes: z.string().max(MAX_NOTES_LENGTH),
  })
  .strict();

export type SavedCart = z.infer<typeof savedCartSchema>;

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null; // Storage blocked (privacy settings): carts are in memory only.
  }
}

export function clearSavedCart(ownerId: string): void {
  try {
    storage()?.removeItem(cartStorageKey(ownerId));
  } catch {
    // Nothing to do: the cart was not readable either.
  }
}

/** This retailer's saved cart, or null (and the entry removed) if unusable. */
export function readSavedCart(ownerId: string): SavedCart | null {
  let raw: string | null;
  try {
    raw = storage()?.getItem(cartStorageKey(ownerId)) ?? null;
  } catch {
    return null;
  }
  if (raw === null) {
    return null;
  }
  try {
    const result = savedCartSchema.safeParse(JSON.parse(raw));
    if (result.success && result.data.ownerId === ownerId) {
      return result.data;
    }
  } catch {
    // Not JSON: treated like any other malformed entry.
  }
  clearSavedCart(ownerId);
  return null;
}

/** Saves the cart, or removes the entry when the cart is empty. */
export function writeSavedCart(ownerId: string, lines: readonly CartLine[], notes: string): void {
  if (lines.length === 0 && notes === "") {
    clearSavedCart(ownerId);
    return;
  }
  const saved: SavedCart = {
    version: 1,
    ownerId,
    lines: lines.map((line) => ({ productId: line.product.id, quantity: line.quantity })),
    notes,
  };
  try {
    storage()?.setItem(cartStorageKey(ownerId), JSON.stringify(saved));
  } catch {
    // Quota exceeded or storage blocked: the cart stays in memory.
  }
}

/** One product of the signed-in retailer's catalogue (404 if no longer offered). */
export async function fetchCatalogueProduct(productId: string): Promise<CatalogueProduct> {
  const data = await api.get<unknown>(`/retailer/catalogue/${encodeURIComponent(productId)}`);
  const result = catalogueProductSchema.safeParse(data);
  if (!result.success) {
    throw new Error("Unexpected response from the server.");
  }
  return result.data;
}

/**
 * Cart lines for a saved cart, with current product details. Products that
 * can no longer be loaded (no longer offered to this retailer, or the
 * request failed) are left out; `missing` says how many.
 */
export async function loadSavedLines(
  saved: SavedCart,
): Promise<{ lines: CartLine[]; missing: number }> {
  const loaded = await Promise.all(
    saved.lines.map(async (line) => {
      try {
        return { product: await fetchCatalogueProduct(line.productId), quantity: line.quantity };
      } catch {
        return null;
      }
    }),
  );
  const lines = loaded.filter((line): line is CartLine => line !== null);
  return { lines, missing: saved.lines.length - lines.length };
}
