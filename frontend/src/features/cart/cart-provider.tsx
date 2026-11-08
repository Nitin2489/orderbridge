"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useReducer, useRef, type ReactNode } from "react";

import { useSession } from "@/features/auth/session-provider";
import type { CatalogueProduct } from "@/features/catalogue/types";
import { orderKeys } from "@/features/orders/api";
import type { RetailerOrderDetail } from "@/features/orders/types";
import { isApiError } from "@/lib/api";

import { SUBMIT_ORDER_MUTATION_KEY, submitOrder } from "./api";
import {
  buildPayload,
  cartReducer,
  emptyCart,
  keyForSubmission,
  type CartLine,
  type CartState,
} from "./cart-state";
import { submissionFailure, type SubmissionFailure } from "./errors";
import type { SubmitOrderPayload } from "./types";

/**
 * The signed-in retailer's cart, shared by the catalogue, the cart page and
 * the navigation badge.
 *
 * Mounted once with the app providers (the navigation sits above the
 * /retailer layout), but always scoped to one retailer: the cart remembers
 * whose it is, and is emptied whenever the signed-in retailer changes,
 * including sign-out, session expiry and signing back in. It lives in memory
 * only: nothing is written to browser storage, and a page reload starts an
 * empty cart.
 */

interface SubmitVariables {
  payload: SubmitOrderPayload;
  signature: string;
  ownerId: string;
}

export interface CartContextValue {
  lines: readonly CartLine[];
  notes: string;
  /** Number of distinct products (lines) in the cart. */
  itemCount: number;
  /** True while a submission is in flight: the cart cannot be edited. */
  locked: boolean;
  add: (product: CatalogueProduct, quantity?: number) => void;
  setQuantity: (productId: string, quantity: number) => void;
  remove: (productId: string) => void;
  setNotes: (notes: string) => void;
  submit: () => void;
  submitting: boolean;
  /** Why the last submission failed, or null. */
  failure: SubmissionFailure | null;
  /** After a 429: when submitting is allowed again (epoch ms), else 0. */
  retryAvailableAt: number;
  confirmation: RetailerOrderDetail | null;
  dismissConfirmation: () => void;
}

const CartContext = createContext<CartContextValue | null>(null);

function newClientRequestId(): string {
  return globalThis.crypto.randomUUID();
}

export function CartProvider({ children }: { children: ReactNode }) {
  const { user } = useSession();
  const ownerId = user?.role === "retailer" ? (user.retailer?.id ?? null) : null;

  const [state, dispatch] = useReducer(cartReducer, ownerId, emptyCart);
  // A different retailer (or none) since the last render: start an empty
  // cart. Dispatching during render is React's pattern for resetting state
  // when an input changes; this render already uses the empty cart.
  if (state.ownerId !== ownerId) {
    dispatch({ type: "reset", ownerId });
  }
  const cart: CartState = state.ownerId === ownerId ? state : emptyCart(ownerId);

  const inFlight = useRef(false);
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationKey: SUBMIT_ORDER_MUTATION_KEY,
    mutationFn: ({ payload }: SubmitVariables) => submitOrder(payload),
    // Runs even if the cart page has unmounted meanwhile.
    onSuccess: (order, { signature, ownerId: submittedBy }) => {
      dispatch({ type: "submitted", order, signature, ownerId: submittedBy });
      // Order history now has a new order.
      void queryClient.invalidateQueries({ queryKey: orderKeys.all });
    },
    onError: (error) => {
      // A 409 means this key belongs to another request: never reuse it.
      if (submissionFailure(error).conflict) {
        dispatch({ type: "abandonAttempt" });
      }
    },
  });

  // Only this retailer's own outcome is ever shown.
  const ownOutcome = ownerId !== null && mutation.variables?.ownerId === ownerId;
  const failure = ownOutcome && mutation.isError ? submissionFailure(mutation.error) : null;
  const retryAfter =
    ownOutcome && isApiError(mutation.error) && mutation.error.status === 429
      ? mutation.error.retryAfterSeconds
      : null;

  const submit = () => {
    if (inFlight.current || mutation.isPending || !ownerId || cart.lines.length === 0) {
      return;
    }
    // Same payload as a pending attempt: same key (a retry). Otherwise a
    // new key: a new submission.
    const attempt = keyForSubmission(cart, newClientRequestId);
    dispatch({ type: "startAttempt", attempt });
    inFlight.current = true;
    mutation.mutate(
      {
        payload: buildPayload(cart, attempt.clientRequestId),
        signature: attempt.signature,
        ownerId,
      },
      {
        onSettled: () => {
          inFlight.current = false;
        },
      },
    );
  };

  const locked = mutation.isPending;
  const value: CartContextValue = {
    lines: cart.lines,
    notes: cart.notes,
    itemCount: cart.lines.length,
    locked,
    add: (product, quantity) => {
      // No signed-in retailer, no cart.
      if (!locked && ownerId) {
        dispatch({ type: "add", product, quantity });
      }
    },
    setQuantity: (productId, quantity) => {
      if (!locked) {
        dispatch({ type: "setQuantity", productId, quantity });
      }
    },
    remove: (productId) => {
      if (!locked) {
        dispatch({ type: "remove", productId });
      }
    },
    setNotes: (notes) => {
      if (!locked) {
        dispatch({ type: "setNotes", notes });
      }
    },
    submit,
    submitting: mutation.isPending,
    failure,
    retryAvailableAt:
      retryAfter && mutation.submittedAt ? mutation.submittedAt + retryAfter * 1000 : 0,
    confirmation: cart.confirmation,
    dismissConfirmation: () => dispatch({ type: "dismissConfirmation" }),
  };

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart(): CartContextValue {
  const cart = useContext(CartContext);
  if (cart === null) {
    throw new Error("useCart must be used inside CartProvider.");
  }
  return cart;
}
