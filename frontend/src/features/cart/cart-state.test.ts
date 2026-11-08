import { describe, expect, it } from "vitest";

import { biscuits, detergent, makeOrder, makeProduct, tea } from "@/test/cart-fixtures";

import {
  buildPayload,
  cartReducer,
  emptyCart,
  keyForSubmission,
  MAX_LINE_QUANTITY,
  MAX_ORDER_LINES,
  orderSignature,
  type CartState,
} from "./cart-state";

const OWNER = "retailer-a";

function cartWith(...actions: Parameters<typeof cartReducer>[1][]): CartState {
  return actions.reduce(cartReducer, emptyCart(OWNER));
}

describe("cart lines", () => {
  it("adds a product with quantity 1", () => {
    const cart = cartWith({ type: "add", product: biscuits });

    expect(cart.lines).toEqual([{ product: biscuits, quantity: 1 }]);
  });

  it("keeps one line per product: adding again increases its quantity", () => {
    const cart = cartWith(
      { type: "add", product: biscuits },
      { type: "add", product: detergent },
      { type: "add", product: biscuits, quantity: 4 },
    );

    expect(cart.lines.map((line) => [line.product.id, line.quantity])).toEqual([
      [biscuits.id, 5],
      [detergent.id, 1],
    ]);
  });

  it("updates, increments and decrements a quantity, never below 1", () => {
    let cart = cartWith({ type: "add", product: biscuits });
    cart = cartReducer(cart, { type: "setQuantity", productId: biscuits.id, quantity: 12 });
    expect(cart.lines[0]?.quantity).toBe(12);
    cart = cartReducer(cart, { type: "setQuantity", productId: biscuits.id, quantity: 13 });
    expect(cart.lines[0]?.quantity).toBe(13);
    cart = cartReducer(cart, { type: "setQuantity", productId: biscuits.id, quantity: 0 });
    expect(cart.lines[0]?.quantity).toBe(1);
    cart = cartReducer(cart, { type: "setQuantity", productId: biscuits.id, quantity: -5 });
    expect(cart.lines[0]?.quantity).toBe(1);
  });

  it("caps quantities at the backend maximum", () => {
    let cart = cartWith({ type: "add", product: biscuits, quantity: MAX_LINE_QUANTITY + 50 });
    expect(cart.lines[0]?.quantity).toBe(MAX_LINE_QUANTITY);
    cart = cartReducer(cart, { type: "add", product: biscuits });
    expect(cart.lines[0]?.quantity).toBe(MAX_LINE_QUANTITY);
    cart = cartReducer(cart, { type: "setQuantity", productId: biscuits.id, quantity: 1e9 });
    expect(cart.lines[0]?.quantity).toBe(MAX_LINE_QUANTITY);
  });

  it("removes a line", () => {
    const cart = cartWith(
      { type: "add", product: biscuits },
      { type: "add", product: detergent },
      { type: "remove", productId: biscuits.id },
    );

    expect(cart.lines.map((line) => line.product.id)).toEqual([detergent.id]);
  });

  it("refuses out-of-stock and unorderable products", () => {
    const misconfigured = makeProduct({
      id: "55555555-5555-4555-8555-555555555555",
      scheme_status: "configuration_error",
    });

    const cart = cartWith(
      { type: "add", product: tea },
      { type: "add", product: misconfigured },
    );

    expect(cart.lines).toEqual([]);
  });

  it("allows at most the backend's number of lines", () => {
    const actions = Array.from({ length: MAX_ORDER_LINES + 1 }, (_, index) => ({
      type: "add" as const,
      product: makeProduct({ id: `product-${index}` }),
    }));

    expect(cartWith(...actions).lines).toHaveLength(MAX_ORDER_LINES);
  });
});

describe("payload", () => {
  it("contains only the key, product ids, quantities and notes", () => {
    const cart = cartWith(
      { type: "add", product: biscuits, quantity: 10 },
      { type: "add", product: detergent, quantity: 5 },
      { type: "setNotes", notes: "  Deliver before noon  " },
    );

    const payload = buildPayload(cart, "key-1");

    expect(payload).toEqual({
      client_request_id: "key-1",
      items: [
        { product_id: biscuits.id, quantity: 10 },
        { product_id: detergent.id, quantity: 5 },
      ],
      notes: "Deliver before noon",
    });
    expect(Object.keys(payload).sort()).toEqual(["client_request_id", "items", "notes"]);
    for (const item of payload.items) {
      expect(Object.keys(item).sort()).toEqual(["product_id", "quantity"]);
    }
  });

  it("sends empty notes as null", () => {
    const cart = cartWith(
      { type: "add", product: biscuits },
      { type: "setNotes", notes: "   " },
    );

    expect(buildPayload(cart, "key").notes).toBeNull();
  });
});

describe("idempotency key", () => {
  const keys = (...values: string[]) => {
    const queue = [...values];
    return () => queue.shift() ?? "unexpected";
  };

  it("uses a new key for a first submission", () => {
    const cart = cartWith({ type: "add", product: biscuits });

    expect(keyForSubmission(cart, keys("new")).clientRequestId).toBe("new");
  });

  it("reuses the pending key while the payload is unchanged", () => {
    let cart = cartWith({ type: "add", product: biscuits });
    const first = keyForSubmission(cart, keys("first"));
    cart = cartReducer(cart, { type: "startAttempt", attempt: first });

    expect(keyForSubmission(cart, keys("second"))).toEqual(first);
  });

  it("uses a new key once the cart has changed", () => {
    let cart = cartWith({ type: "add", product: biscuits });
    cart = cartReducer(cart, {
      type: "startAttempt",
      attempt: keyForSubmission(cart, keys("first")),
    });
    cart = cartReducer(cart, { type: "setQuantity", productId: biscuits.id, quantity: 3 });

    expect(keyForSubmission(cart, keys("second")).clientRequestId).toBe("second");
  });

  it("clears the submitted cart and keeps the confirmation", () => {
    let cart = cartWith({ type: "add", product: biscuits });
    const signature = orderSignature(cart);
    const order = makeOrder();

    cart = cartReducer(cart, { type: "submitted", order, signature, ownerId: OWNER });

    expect(cart.lines).toEqual([]);
    expect(cart.attempt).toBeNull();
    expect(cart.confirmation).toBe(order);
  });

  it("ignores a response for another retailer", () => {
    const cart = cartWith({ type: "add", product: biscuits });

    const after = cartReducer(cart, {
      type: "submitted",
      order: makeOrder(),
      signature: orderSignature(cart),
      ownerId: "retailer-b",
    });

    expect(after).toBe(cart);
  });
});
