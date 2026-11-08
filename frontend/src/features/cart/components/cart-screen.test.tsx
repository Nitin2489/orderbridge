import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SESSION_QUERY_KEY } from "@/features/auth/api";
import type { CatalogueProduct } from "@/features/catalogue/types";
import { orderKeys } from "@/features/orders/api";
import { biscuits, detergent, makeOrder, makeProduct } from "@/test/cart-fixtures";
import {
  deferred,
  json,
  mockApi,
  networkFailure,
  retailerUser,
  sequence,
} from "@/test/api-mock";
import { renderWithSession } from "@/test/render";

import { useCart } from "../cart-provider";
import { CartScreen } from "./cart-screen";

const ORDERS = "POST /retailer/orders";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function CartWithProducts({ products }: { products: CatalogueProduct[] }) {
  const cart = useCart();
  return (
    <>
      <p>Lines: {cart.itemCount}</p>
      <button type="button" onClick={() => products.forEach((product) => cart.add(product, 10))}>
        Fill cart
      </button>
      <CartScreen />
    </>
  );
}

async function renderCart(
  routes: Parameters<typeof mockApi>[0] = {},
  products: CatalogueProduct[] = [biscuits, detergent],
) {
  const backend = mockApi({ "GET /auth/me": json(200, retailerUser), ...routes });
  const user = userEvent.setup();
  const { queryClient } = renderWithSession(<CartWithProducts products={products} />);
  await waitFor(() =>
    expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(retailerUser),
  );
  await user.click(screen.getByRole("button", { name: "Fill cart" }));
  return { backend, user, submissions: () => backend.calls(ORDERS) };
}

function submitButton() {
  return screen.getByRole("button", { name: /submit|try again/i });
}

describe("CartScreen", () => {
  it("shows an empty cart with a way back to the catalogue", async () => {
    mockApi({ "GET /auth/me": json(200, retailerUser) });
    renderWithSession(<CartScreen />);

    expect(await screen.findByRole("heading", { name: "Your cart is empty" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Browse the catalogue" })).toHaveAttribute(
      "href",
      "/retailer",
    );
  });

  it("lists each product with its SKU, stock and quantity in its unit", async () => {
    await renderCart();

    const line = screen.getByRole("listitem", { name: "Glucose biscuit 75 g" });
    expect(within(line).getByText("SKU-BS-075")).toBeInTheDocument();
    expect(within(line).getByText(/340 cases in stock/)).toBeInTheDocument();
    expect(
      within(line).getByRole("textbox", { name: "Quantity of Glucose biscuit 75 g, in cases" }),
    ).toHaveValue("10");
    // The line's exact estimate at the catalogue price: 10 x 777.60.
    expect(within(line).getByText("₹7,776.00")).toBeInTheDocument();
  });

  it("shows an exact estimated subtotal, clearly labelled as an estimate", async () => {
    await renderCart();

    const summary = screen.getByRole("region", { name: "Order summary" });
    // 10 x 777.60 + 10 x 2030.00, before schemes, from catalogue prices.
    expect(within(summary).getByText("₹28,076.00")).toBeInTheDocument();
    expect(within(summary).getByText("Estimated subtotal")).toBeInTheDocument();
    expect(summary).toHaveTextContent("before scheme discounts and free goods");
    expect(summary).toHaveTextContent("Excludes GST");
    expect(summary).toHaveTextContent("the order desk confirms the final amount");
    // No grand total is invented: the backend prices the order.
    expect(screen.queryByText(/grand total/i)).not.toBeInTheDocument();
  });

  it("says whether the quantity reaches the product's scheme", async () => {
    const withScheme = makeProduct({
      scheme_status: "active",
      scheme: {
        id: "55555555-5555-4555-8555-555555555555",
        name: "Buy 10 cases, get 1 free",
        scheme_type: "free_goods",
        minimum_quantity: 10,
        free_quantity: 1,
        discount_percent: "0.00",
        starts_at: "2026-01-01T00:00:00Z",
        ends_at: null,
        reference: null,
      },
    });
    const { user } = await renderCart({}, [withScheme]);
    const line = screen.getByRole("listitem", { name: "Glucose biscuit 75 g" });
    const field = within(line).getByRole("textbox", {
      name: "Quantity of Glucose biscuit 75 g, in cases",
    });

    // Filled with 10: the threshold is reached.
    expect(within(line).getByText(/This quantity qualifies/)).toBeInTheDocument();

    await user.clear(field);
    await user.type(field, "7");
    await user.tab();

    expect(within(line).getByText("Order 3 more cases to qualify.")).toBeInTheDocument();
  });

  it("removes a product", async () => {
    const { user } = await renderCart();

    await user.click(screen.getByRole("button", { name: "Remove Glucose biscuit 75 g from cart" }));

    expect(screen.getByText("Lines: 1")).toBeInTheDocument();
    expect(screen.queryByRole("listitem", { name: "Glucose biscuit 75 g" })).not.toBeInTheDocument();
  });

  it("submits only ids, quantities and notes, then shows the server's confirmation", async () => {
    const { user, submissions } = await renderCart({ [ORDERS]: json(201, makeOrder()) });

    await user.type(
      screen.getByLabelText("Notes for the order desk (optional)"),
      "  Please call first ",
    );
    await user.click(submitButton());

    expect(await screen.findByRole("heading", { name: "Order submitted" })).toBeInTheDocument();
    const [request] = submissions();
    expect(request?.body).toEqual({
      client_request_id: expect.stringMatching(UUID),
      items: [
        { product_id: biscuits.id, quantity: 10 },
        { product_id: detergent.id, quantity: 10 },
      ],
      notes: "Please call first",
    });
    expect(screen.getByText("OB-2026-000123")).toBeInTheDocument();
    expect(screen.getByText("Pending review")).toBeInTheDocument();
    expect(screen.getByText(/not confirmed yet/)).toBeInTheDocument();
    expect(screen.getByText(/may differ/)).toBeInTheDocument();
    // The submitted cart is cleared.
    expect(screen.getByText("Lines: 0")).toBeInTheDocument();
    // The confirmation links to the new order.
    expect(screen.getByRole("link", { name: "View order details" })).toHaveAttribute(
      "href",
      `/retailer/orders/${makeOrder().id}`,
    );
  });

  it("refreshes the order history after a successful submission", async () => {
    mockApi({ "GET /auth/me": json(200, retailerUser), [ORDERS]: json(201, makeOrder()) });
    const user = userEvent.setup();
    const { queryClient } = renderWithSession(<CartWithProducts products={[biscuits]} />);
    await waitFor(() =>
      expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(retailerUser),
    );
    queryClient.setQueryData(orderKeys.list(null), { pages: [], pageParams: [] });
    await user.click(screen.getByRole("button", { name: "Fill cart" }));

    await user.click(submitButton());

    await screen.findByRole("heading", { name: "Order submitted" });
    expect(queryClient.getQueryState(orderKeys.list(null))?.isInvalidated).toBe(true);
  });

  it("treats 200 (the same request again) like 201", async () => {
    await renderCart({ [ORDERS]: json(200, makeOrder()) });

    await userEvent.setup().click(submitButton());

    expect(await screen.findByRole("heading", { name: "Order submitted" })).toBeInTheDocument();
    expect(screen.getByText("Lines: 0")).toBeInTheDocument();
  });

  it("shows line issues from a 422 and keeps the cart", async () => {
    const { user } = await renderCart({
      [ORDERS]: json(422, {
        detail: "Some order lines cannot be ordered. No order was created.",
        issues: [
          {
            line_number: 2,
            product_id: detergent.id,
            code: "insufficient_stock",
            message: "Insufficient stock for Detergent powder 1 kg.",
          },
        ],
      }),
    });

    await user.click(submitButton());

    expect(await screen.findByRole("alert")).toHaveTextContent("No order was created");
    const line = screen.getByRole("listitem", { name: "Detergent powder 1 kg" });
    expect(line).toHaveAccessibleDescription("Insufficient stock for Detergent powder 1 kg.");
    expect(
      within(screen.getByRole("listitem", { name: "Glucose biscuit 75 g" })).queryByText(
        /Insufficient/,
      ),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Lines: 2")).toBeInTheDocument();
    expect(submitButton()).toBeEnabled();
  });

  it("retries an uncertain failure with the same key and payload", async () => {
    const { user, submissions } = await renderCart({
      [ORDERS]: sequence(networkFailure(), json(201, makeOrder())),
    });

    await user.click(submitButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("cannot create a second order");
    expect(screen.getByText("Lines: 2")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Retry submitting order" }));

    expect(await screen.findByRole("heading", { name: "Order submitted" })).toBeInTheDocument();
    const [first, second] = submissions();
    expect(second?.body).toEqual(first?.body);
  });

  it("uses a new key once the cart changes after a failure", async () => {
    const { user, submissions } = await renderCart({
      [ORDERS]: sequence(networkFailure(), json(201, makeOrder())),
    });

    await user.click(submitButton());
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Increase quantity of Glucose biscuit 75 g" }));
    await user.click(submitButton());

    await screen.findByRole("heading", { name: "Order submitted" });
    const [first, second] = submissions().map(
      (request) => request.body as { client_request_id: string },
    );
    expect(second?.client_request_id).not.toBe(first?.client_request_id);
  });

  it("sends one request when Submit is pressed repeatedly, and locks the cart", async () => {
    const slow = deferred(json(201, makeOrder()));
    const { user, submissions } = await renderCart({ [ORDERS]: slow.handler });

    await user.click(submitButton());
    const pending = screen.getByRole("button", { name: /Submitting order/ });
    expect(pending).toBeDisabled();
    await user.click(pending);
    expect(
      screen.getByRole("button", { name: "Increase quantity of Glucose biscuit 75 g" }),
    ).toBeDisabled();
    expect(submissions()).toHaveLength(1);

    act(() => slow.resolve());
    expect(await screen.findByRole("heading", { name: "Order submitted" })).toBeInTheDocument();
    expect(submissions()).toHaveLength(1);
  });

  it("explains a 409 and uses a new key for the next submission", async () => {
    const { user, submissions } = await renderCart({
      [ORDERS]: sequence(
        json(409, { detail: "This client_request_id was already used for a different order." }),
        json(201, makeOrder()),
      ),
    });

    await user.click(submitButton());
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "conflicts with an earlier request, so no new order was created",
    );
    expect(screen.getByText("Lines: 2")).toBeInTheDocument();

    await user.click(submitButton());
    await screen.findByRole("heading", { name: "Order submitted" });
    const [first, second] = submissions().map(
      (request) => request.body as { client_request_id: string },
    );
    expect(second?.client_request_id).not.toBe(first?.client_request_id);
  });

  it("explains a 403", async () => {
    const { user } = await renderCart({
      [ORDERS]: json(403, { detail: "You do not have permission to perform this action." }),
    });

    await user.click(submitButton());

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Your account cannot place orders right now.",
    );
    expect(screen.getByText("Lines: 2")).toBeInTheDocument();
  });

  it("waits out a 429's Retry-After and keeps the cart", async () => {
    const { user } = await renderCart({
      [ORDERS]: json(429, { detail: "Too many requests." }, { "Retry-After": "120" }),
    });

    await user.click(submitButton());

    expect(await screen.findByRole("alert")).toHaveTextContent("Please wait 2 minutes");
    expect(screen.getByRole("button", { name: /Try again in [12]:\d\d/ })).toBeDisabled();
    expect(screen.getByText("Lines: 2")).toBeInTheDocument();
  });

  it("never shows server error details", async () => {
    const { user } = await renderCart({
      [ORDERS]: json(500, { detail: "Traceback (most recent call last): secret" }),
    });

    await user.click(submitButton());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong on our side.");
    expect(alert).not.toHaveTextContent("Traceback");
    expect(screen.getByRole("button", { name: "Retry submitting order" })).toBeEnabled();
  });
});
