import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SESSION_QUERY_KEY } from "@/features/auth/api";
import { biscuits, detergent, makeOrder } from "@/test/cart-fixtures";
import { json, mockApi, noContent, retailerUser } from "@/test/api-mock";
import { renderWithSession } from "@/test/render";

import { useCart } from "./cart-provider";
import { cartStorageKey } from "./cart-storage";

const OWNER = retailerUser.retailer.id;
const KEY = cartStorageKey(OWNER);
const PRODUCT = (id: string) => `GET /retailer/catalogue/${id}`;

const otherRetailer = {
  ...retailerUser,
  id: "8b2c3d4e-5f6a-4b7c-8d9e-555555555555",
  retailer: { ...retailerUser.retailer, id: "9c3d4e5f-6a7b-4c8d-9e0f-666666666666" },
};

function CartProbe() {
  const cart = useCart();
  return (
    <>
      <p>Lines: {cart.itemCount}</p>
      <p>
        Quantities: {cart.lines.map((line) => `${line.product.sku}=${line.quantity}`).join(",")}
      </p>
      <p>Notes: {cart.notes}</p>
      <p>Unrestored: {cart.unrestoredCount}</p>
      <button type="button" onClick={() => cart.add(biscuits, 3)}>
        Add biscuits
      </button>
      <button type="button" onClick={() => cart.setNotes("Before noon")}>
        Add notes
      </button>
      <button type="button" onClick={() => cart.submit()}>
        Submit
      </button>
    </>
  );
}

async function signedIn(routes: Parameters<typeof mockApi>[0] = {}, user = retailerUser) {
  const backend = mockApi({ "GET /auth/me": json(200, user), ...routes });
  const result = renderWithSession(<CartProbe />);
  await waitFor(() => expect(result.queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(user));
  return { ...result, backend, events: userEvent.setup() };
}

function save(value: unknown, key = KEY) {
  window.localStorage.setItem(key, typeof value === "string" ? value : JSON.stringify(value));
}

const validSaved = {
  version: 1,
  ownerId: OWNER,
  lines: [
    { productId: biscuits.id, quantity: 3 },
    { productId: detergent.id, quantity: 5 },
  ],
  notes: "Before noon",
};

describe("saved cart", () => {
  it("saves only product ids, quantities and notes for this retailer", async () => {
    const { events } = await signedIn();

    await events.click(screen.getByRole("button", { name: "Add biscuits" }));
    await events.click(screen.getByRole("button", { name: "Add notes" }));

    const saved = JSON.parse(window.localStorage.getItem(KEY)!);
    expect(saved).toEqual({
      version: 1,
      ownerId: OWNER,
      lines: [{ productId: biscuits.id, quantity: 3 }],
      notes: "Before noon",
    });
    // No prices, stock, credit or account details.
    const raw = window.localStorage.getItem(KEY)!;
    expect(raw).not.toContain(biscuits.unit_price);
    expect(raw).not.toContain(retailerUser.phone_number);
  });

  it("restores the cart after a reload with current product details", async () => {
    save(validSaved);

    const { backend } = await signedIn({
      [PRODUCT(biscuits.id)]: json(200, biscuits),
      [PRODUCT(detergent.id)]: json(200, detergent),
    });

    expect(await screen.findByText("Lines: 2")).toBeInTheDocument();
    expect(
      screen.getByText(`Quantities: ${biscuits.sku}=3,${detergent.sku}=5`),
    ).toBeInTheDocument();
    expect(screen.getByText("Notes: Before noon")).toBeInTheDocument();
    expect(backend.calls(PRODUCT(biscuits.id))).toHaveLength(1);
    expect(screen.getByText("Unrestored: 0")).toBeInTheDocument();
  });

  it("says how many saved products are no longer offered, and keeps the rest", async () => {
    save(validSaved);

    await signedIn({
      [PRODUCT(biscuits.id)]: json(200, biscuits),
      [PRODUCT(detergent.id)]: json(404, { detail: "Product not found." }),
    });

    expect(await screen.findByText("Lines: 1")).toBeInTheDocument();
    expect(screen.getByText("Unrestored: 1")).toBeInTheDocument();
  });

  it("never restores one retailer's cart for another retailer", async () => {
    save(validSaved);

    const { backend } = await signedIn({}, otherRetailer);

    expect(screen.getByText("Lines: 0")).toBeInTheDocument();
    expect(backend.calls(PRODUCT(biscuits.id))).toHaveLength(0);
    // The first retailer's saved cart is left alone for them.
    expect(window.localStorage.getItem(KEY)).not.toBeNull();
  });

  it.each([
    ["not JSON", "{not json"],
    ["another version", { ...validSaved, version: 2 }],
    ["another owner under this key", { ...validSaved, ownerId: otherRetailer.retailer.id }],
    ["an unexpected field", { ...validSaved, credit_limit: "50000.00" }],
    ["an invalid quantity", { ...validSaved, lines: [{ productId: biscuits.id, quantity: -1 }] }],
    ["a malformed product id", { ...validSaved, lines: [{ productId: "x", quantity: 1 }] }],
  ])("ignores and removes a saved cart with %s", async (_label, value) => {
    save(value);

    const { backend } = await signedIn();

    expect(screen.getByText("Lines: 0")).toBeInTheDocument();
    expect(backend.calls(PRODUCT(biscuits.id))).toHaveLength(0);
    await waitFor(() => expect(window.localStorage.getItem(KEY)).toBeNull());
  });

  it("is removed after a successful order", async () => {
    const { events } = await signedIn({ "POST /retailer/orders": json(201, makeOrder()) });
    await events.click(screen.getByRole("button", { name: "Add biscuits" }));
    expect(window.localStorage.getItem(KEY)).not.toBeNull();

    await events.click(screen.getByRole("button", { name: "Submit" }));

    expect(await screen.findByText("Lines: 0")).toBeInTheDocument();
    expect(window.localStorage.getItem(KEY)).toBeNull();
  });

  it("is removed on sign-out and not restored on the next sign-in", async () => {
    const { events, queryClient } = await signedIn({ "POST /auth/logout": noContent() });
    await events.click(screen.getByRole("button", { name: "Add biscuits" }));
    expect(window.localStorage.getItem(KEY)).not.toBeNull();

    act(() => queryClient.setQueryData(SESSION_QUERY_KEY, null));

    await waitFor(() => expect(window.localStorage.getItem(KEY)).toBeNull());
    act(() => queryClient.setQueryData(SESSION_QUERY_KEY, retailerUser));
    expect(await screen.findByText("Lines: 0")).toBeInTheDocument();
  });
});
