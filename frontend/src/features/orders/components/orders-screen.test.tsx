import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { makeOrderPage, makeOrderSummary } from "@/test/cart-fixtures";
import {
  json,
  mockApi,
  retailerUser,
  sequence,
  type RecordedRequest,
} from "@/test/api-mock";
import { renderWithSession } from "@/test/render";

import { OrdersScreen } from "./orders-screen";

const LIST = "GET /retailer/orders";

const pending = makeOrderSummary();
const confirmed = makeOrderSummary({
  id: "55555555-5555-4555-8555-555555555555",
  order_number: "OB-2026-000124",
  status: "confirmed",
  item_count: 3,
  grand_total: "12500.00",
  created_at: "2026-11-05T09:00:00Z",
  confirmed_at: "2026-11-05T12:00:00Z",
});
const draft = makeOrderSummary({
  id: "66666666-6666-4666-8666-666666666666",
  order_number: "OB-2026-000125",
  status: "draft",
});

function renderOrders(list: Parameters<typeof mockApi>[0][string]) {
  const backend = mockApi({ "GET /auth/me": json(200, retailerUser), [LIST]: list });
  const user = userEvent.setup();
  renderWithSession(<OrdersScreen />);
  return { backend, user, lastQuery: () => backend.calls(LIST).at(-1)?.query };
}

describe("OrdersScreen", () => {
  it("shows a loading state, then each order with a link to its details", async () => {
    const { lastQuery } = renderOrders(json(200, makeOrderPage([pending, confirmed])));

    expect(screen.getByText("Loading orders…")).toBeInTheDocument();
    const row = await screen.findByRole("link", { name: /OB-2026-000123/ });

    expect(row).toHaveAttribute("href", `/retailer/orders/${pending.id}`);
    expect(row).toHaveTextContent("Placed 7 Nov 2026 · 1 product");
    expect(row).toHaveTextContent("Pending review");
    expect(row).toHaveTextContent("Estimated total ₹7,776.00");

    const confirmedRow = screen.getByRole("link", { name: /OB-2026-000124/ });
    expect(confirmedRow).toHaveTextContent("3 products");
    expect(confirmedRow).toHaveTextContent("Confirmed");
    expect(confirmedRow).toHaveTextContent("Total ₹12,500.00");
    expect(confirmedRow).not.toHaveTextContent("Estimated");

    expect(screen.getByText(/Amounts exclude GST/)).toBeInTheDocument();
    expect(screen.getByText(/may change when the order desk confirms them/)).toBeInTheDocument();
    expect(lastQuery()?.get("limit")).toBe("20");
    expect(lastQuery()?.get("offset")).toBe("0");
    expect(lastQuery()?.has("status")).toBe(false);
  });

  it("explains an empty history with a way to the catalogue", async () => {
    renderOrders(json(200, makeOrderPage([])));

    expect(await screen.findByRole("heading", { name: "No orders yet" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Browse the catalogue" })).toHaveAttribute(
      "href",
      "/retailer",
    );
  });

  it("shows an error with a working retry", async () => {
    const { user, backend } = renderOrders(
      sequence(json(500, { detail: "Internal Server Error" }), json(200, makeOrderPage([pending]))),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong");
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("link", { name: /OB-2026-000123/ })).toBeInTheDocument();
    expect(backend.calls(LIST)).toHaveLength(2);
  });

  it("loads more orders with the backend's offset and total", async () => {
    const { user, lastQuery } = renderOrders((request: RecordedRequest) =>
      request.query.get("offset") === "1"
        ? json(200, makeOrderPage([confirmed], { total: 2, offset: 1 }))(request)
        : json(200, makeOrderPage([pending], { total: 2 }))(request),
    );

    await screen.findByRole("link", { name: /OB-2026-000123/ });
    expect(screen.getByText("Showing 1 of 2 orders")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Show more orders" }));

    expect(await screen.findByRole("link", { name: /OB-2026-000124/ })).toBeInTheDocument();
    expect(lastQuery()?.get("offset")).toBe("1");
    expect(screen.queryByRole("button", { name: "Show more orders" })).not.toBeInTheDocument();
  });

  it("filters by status and offers no draft filter", async () => {
    const { user, lastQuery } = renderOrders((request: RecordedRequest) =>
      request.query.get("status") === "confirmed"
        ? json(200, makeOrderPage([confirmed]))(request)
        : json(200, makeOrderPage([pending, confirmed]))(request),
    );
    await screen.findByRole("link", { name: /OB-2026-000123/ });

    const filters = screen.getByRole("group", { name: "Filter by status" });
    expect(within(filters).queryByRole("button", { name: "Draft" })).not.toBeInTheDocument();
    expect(
      within(filters)
        .getAllByRole("button")
        .map((button) => button.textContent),
    ).toEqual(["All", "Pending review", "On hold", "Confirmed", "Rejected", "Exported"]);

    const confirmedChip = within(filters).getByRole("button", { name: "Confirmed" });
    await user.click(confirmedChip);

    expect(confirmedChip).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => expect(lastQuery()?.get("status")).toBe("confirmed"));
    await waitFor(() =>
      expect(screen.queryByRole("link", { name: /OB-2026-000123/ })).not.toBeInTheDocument(),
    );
  });

  it("explains an empty filter and shows all orders again", async () => {
    const { user } = renderOrders((request: RecordedRequest) =>
      request.query.get("status") === "rejected"
        ? json(200, makeOrderPage([]))(request)
        : json(200, makeOrderPage([pending]))(request),
    );
    await screen.findByRole("link", { name: /OB-2026-000123/ });

    await user.click(screen.getByRole("button", { name: "Rejected" }));
    expect(
      await screen.findByRole("heading", { name: "No rejected orders" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Show all orders" }));
    expect(await screen.findByRole("link", { name: /OB-2026-000123/ })).toBeInTheDocument();
  });

  it("still shows a draft order if the backend returns one", async () => {
    renderOrders(json(200, makeOrderPage([draft])));

    const row = await screen.findByRole("link", { name: /OB-2026-000125/ });
    expect(row).toHaveTextContent("Draft");
  });
});
