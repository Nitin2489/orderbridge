import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SESSION_QUERY_KEY } from "@/features/auth/api";
import { makeOrder } from "@/test/cart-fixtures";
import {
  deferred,
  json,
  mockApi,
  networkFailure,
  retailerUser,
  sequence,
} from "@/test/api-mock";
import { renderWithSession } from "@/test/render";

import { orderKeys } from "../api";
import { OrderDetailScreen } from "./order-detail-screen";

const source = makeOrder({ notes: "Deliver before noon" });
const created = makeOrder({
  id: "77777777-7777-4777-8777-777777777777",
  order_number: "OB-2026-000200",
  notes: null,
});
const DETAIL = `GET /retailer/orders/${source.id}`;
const REPEAT = `POST /retailer/orders/${source.id}/repeat`;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const otherRetailer = {
  ...retailerUser,
  id: "8b2c3d4e-5f6a-4b7c-8d9e-555555555555",
  retailer: {
    id: "9c3d4e5f-6a7b-4c8d-9e0f-666666666666",
    business_name: "Gupta Kirana Store",
    owner_name: "Ravi Gupta",
    address: "Wright Town, Jabalpur",
  },
};

async function renderDetail(
  routes: Parameters<typeof mockApi>[0] = {},
  orderId: string = source.id,
) {
  const backend = mockApi({
    "GET /auth/me": json(200, retailerUser),
    [DETAIL]: json(200, source),
    ...routes,
  });
  const user = userEvent.setup();
  const result = renderWithSession(<OrderDetailScreen orderId={orderId} />);
  await waitFor(() =>
    expect(result.queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(retailerUser),
  );
  return { ...result, backend, user, repeats: () => backend.calls(REPEAT) };
}

async function openRepeat(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "Repeat order" }));
}

function createButton() {
  return screen.getByRole("button", { name: /Create new order|Retry creating order|Try again in/ });
}

function keysOf(requests: { body: unknown }[]): string[] {
  return requests.map((request) => (request.body as { client_request_id: string }).client_request_id);
}

describe("OrderDetailScreen", () => {
  it("shows the order, its lines and its estimated totals", async () => {
    await renderDetail();

    expect(
      await screen.findByRole("heading", { name: "Order OB-2026-000123" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Pending review")).toBeInTheDocument();
    expect(screen.getByText("Waiting for order-desk review.")).toBeInTheDocument();
    expect(screen.getByText(/Placed 7 Nov 2026 · Retailer app · 1 product/)).toBeInTheDocument();

    const line = screen.getByRole("listitem", { name: "Glucose biscuit 75 g" });
    expect(within(line).getByText("SKU-BS-075")).toBeInTheDocument();
    expect(within(line).getByText("Scheme: Buy 10 get 1 free")).toBeInTheDocument();
    const values = within(line)
      .getAllByRole("definition")
      .map((value) => value.textContent);
    expect(values).toEqual(["10", "1", "₹777.60", "₹0.00", "₹7,776.00"]);
    // No unit is invented: the API gives none for order lines.
    expect(screen.queryByText(/\bcases?\b/)).not.toBeInTheDocument();
    // Free goods from the scheme are called out on the line.
    expect(within(line).getByText("+1 free")).toBeInTheDocument();

    // Progress inside OrderBridge only: nothing about delivery.
    const progress = screen.getByRole("region", { name: "Progress" });
    expect(within(progress).getByText("Order desk review")).toBeInTheDocument();
    expect(within(progress).getByText("Sent for billing")).toBeInTheDocument();
    expect(progress).not.toHaveTextContent(/deliver|dispatch/i);

    expect(screen.getByText("Deliver before noon")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Estimated totals" })).toBeInTheDocument();
    expect(screen.getByText("Estimated grand total")).toBeInTheDocument();
    expect(screen.getByText(/Amounts exclude GST/)).toBeInTheDocument();
    expect(screen.getByText(/so they may change/)).toBeInTheDocument();
  });

  it("labels a confirmed order's totals as confirmed", async () => {
    await renderDetail({
      [DETAIL]: json(200, makeOrder({ status: "confirmed", confirmed_at: "2026-11-07T12:00:00Z" })),
    });

    expect(await screen.findByText("Order confirmed.")).toBeInTheDocument();
    expect(screen.getByText("Grand total")).toBeInTheDocument();
    expect(screen.queryByText(/Estimated/)).not.toBeInTheDocument();
  });

  it("treats a malformed id as not found without a request", async () => {
    const { backend } = await renderDetail({}, "not-a-uuid");

    expect(screen.getByRole("heading", { name: "Order not found" })).toBeInTheDocument();
    expect(backend.requests.filter((r) => r.path.startsWith("/retailer/orders"))).toEqual([]);
  });

  it("shows not found for a 404 (missing or another retailer's order)", async () => {
    await renderDetail({ [DETAIL]: json(404, { detail: "Order not found." }) });

    expect(await screen.findByRole("heading", { name: "Order not found" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to your orders" })).toHaveAttribute(
      "href",
      "/retailer/orders",
    );
  });

  it("shows other errors with a working retry", async () => {
    const { user } = await renderDetail({
      [DETAIL]: sequence(json(500, { detail: "boom" }), json(200, source)),
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong on our side");
    expect(screen.getByRole("alert")).not.toHaveTextContent("boom");
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(
      await screen.findByRole("heading", { name: "Order OB-2026-000123" }),
    ).toBeInTheDocument();
  });
});

describe("Repeat order", () => {
  it("explains what repeating does before sending anything, and can be cancelled", async () => {
    const { user, repeats } = await renderDetail();

    await openRepeat(user);

    expect(
      screen.getByRole("heading", { name: "Repeat order OB-2026-000123?" }),
    ).toHaveFocus();
    for (const text of [
      "A new order will be created.",
      "It will contain the same products and quantities.",
      "Current prices, schemes and stock will be checked again.",
      "The new order will go to the order desk for review.",
      "This order will remain unchanged.",
    ]) {
      expect(screen.getByText(text)).toBeInTheDocument();
    }
    expect(repeats()).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Repeat order" })).toBeInTheDocument();
    expect(repeats()).toHaveLength(0);
  });

  it.each([
    [201, "created"],
    [200, "returned for the same request"],
  ])("shows the new order on %i (%s) and refreshes the history", async (status) => {
    const { user, repeats, queryClient } = await renderDetail({
      [REPEAT]: json(status, created),
    });
    queryClient.setQueryData(orderKeys.list(null), { pages: [], pageParams: [] });

    await openRepeat(user);
    await user.click(createButton());

    expect(await screen.findByRole("heading", { name: "Order submitted" })).toBeInTheDocument();
    expect(screen.getByText("OB-2026-000200")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View order details" })).toHaveAttribute(
      "href",
      `/retailer/orders/${created.id}`,
    );
    const [request] = repeats();
    expect(request?.body).toEqual({ client_request_id: expect.stringMatching(UUID) });
    expect(queryClient.getQueryState(orderKeys.list(null))?.isInvalidated).toBe(true);
  });

  it("retries an uncertain failure with the same key", async () => {
    const { user, repeats } = await renderDetail({
      [REPEAT]: sequence(networkFailure(), json(500, { detail: "boom" }), json(201, created)),
    });

    await openRepeat(user);
    await user.click(createButton());
    expect(await screen.findByRole("alert")).toHaveTextContent("cannot create a second order");
    await user.click(screen.getByRole("button", { name: "Retry creating order" }));
    await screen.findByText(/Something went wrong on our side/);
    await user.click(screen.getByRole("button", { name: "Retry creating order" }));

    await screen.findByRole("heading", { name: "Order submitted" });
    const keys = keysOf(repeats());
    expect(keys).toHaveLength(3);
    expect(new Set(keys).size).toBe(1);
  });

  it("uses a new key for a new attempt after a final outcome", async () => {
    const { user, repeats } = await renderDetail({
      [REPEAT]: sequence(
        json(422, {
          detail: "Some order lines cannot be ordered. No order was created.",
          issues: [
            {
              line_number: 1,
              product_id: source.items[0]!.product_id,
              code: "insufficient_stock",
              message: "Insufficient stock for Glucose biscuit 75 g.",
            },
          ],
        }),
        json(201, created),
      ),
    });

    await openRepeat(user);
    await user.click(createButton());
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Create new order" }));
    await screen.findByRole("heading", { name: "Order submitted" });

    const [first, second] = keysOf(repeats());
    expect(second).not.toBe(first);
  });

  it("names the products a 422 refuses", async () => {
    const { user } = await renderDetail({
      [REPEAT]: json(422, {
        detail: "Some order lines cannot be ordered. No order was created.",
        issues: [
          {
            line_number: 1,
            product_id: source.items[0]!.product_id,
            code: "product_unavailable",
            message: "Product does not exist or is inactive.",
          },
        ],
      }),
    });

    await openRepeat(user);
    await user.click(createButton());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("No new order was created");
    expect(within(alert).getByRole("listitem")).toHaveTextContent(
      "Glucose biscuit 75 g: Product does not exist or is inactive.",
    );
  });

  it.each([
    [409, { detail: "conflict" }, "conflicts with an earlier request"],
    [403, { detail: "forbidden" }, "Your account cannot place orders right now"],
    [404, { detail: "Order not found." }, "The original order is no longer available"],
  ])("explains a %i", async (status, body, message) => {
    const { user } = await renderDetail({ [REPEAT]: json(status, body) });

    await openRepeat(user);
    await user.click(createButton());

    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("button", { name: "Create new order" })).toBeEnabled();
  });

  it("uses a new key after a 409 conflict", async () => {
    const { user, repeats } = await renderDetail({
      [REPEAT]: sequence(json(409, { detail: "conflict" }), json(201, created)),
    });

    await openRepeat(user);
    await user.click(createButton());
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Create new order" }));
    await screen.findByRole("heading", { name: "Order submitted" });

    const [first, second] = keysOf(repeats());
    expect(second).not.toBe(first);
  });

  it("waits out a 429's Retry-After", async () => {
    const { user } = await renderDetail({
      [REPEAT]: json(429, { detail: "Too many requests." }, { "Retry-After": "60" }),
    });

    await openRepeat(user);
    await user.click(createButton());

    expect(await screen.findByRole("alert")).toHaveTextContent("Please wait 1 minute");
    expect(screen.getByRole("button", { name: /Try again in [01]:\d\d/ })).toBeDisabled();
  });

  it("sends one request when Create is pressed repeatedly", async () => {
    const slow = deferred(json(201, created));
    const { user, repeats } = await renderDetail({ [REPEAT]: slow.handler });

    await openRepeat(user);
    await user.click(createButton());
    const pending = screen.getByRole("button", { name: /Creating order/ });
    expect(pending).toBeDisabled();
    await user.click(pending);
    expect(repeats()).toHaveLength(1);

    act(() => slow.resolve());
    await screen.findByRole("heading", { name: "Order submitted" });
    expect(repeats()).toHaveLength(1);
  });

  it("never reuses a key across a change of signed-in retailer", async () => {
    const { user, repeats, queryClient } = await renderDetail({
      [REPEAT]: sequence(networkFailure(), json(201, created)),
    });

    await openRepeat(user);
    await user.click(createButton());
    await screen.findByRole("alert");

    // Another retailer signs in, then the first one again.
    act(() => queryClient.setQueryData(SESSION_QUERY_KEY, otherRetailer));
    act(() => queryClient.setQueryData(SESSION_QUERY_KEY, retailerUser));

    await user.click(screen.getByRole("button", { name: "Retry creating order" }));
    await screen.findByRole("heading", { name: "Order submitted" });

    const [first, second] = keysOf(repeats());
    expect(second).not.toBe(first);
  });
});
