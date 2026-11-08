import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { json, mockApi, retailerUser, sequence, type RecordedRequest } from "@/test/api-mock";
import { renderWithSession } from "@/test/render";

import type { CatalogueProduct } from "../types";
import { CatalogueScreen } from "./catalogue-screen";

/*
 * Test fixtures shaped like the backend's CatalogueProduct, using the
 * brief's sample products. They exercise rendering only; they are not app
 * data.
 */
const biscuits: CatalogueProduct = {
  id: "p-biscuits",
  sku: "SKU-BS-075",
  name: "Glucose biscuit 75 g",
  description: null,
  brand: "Sample Foods",
  category: "Biscuits",
  unit_name: "case",
  units_per_case: 96,
  stock_quantity: 340,
  is_available: true,
  unit_price: "777.60",
  tax_treatment: "exclusive",
  scheme_status: "active",
  scheme: {
    id: "s-biscuits",
    name: "Buy 10 get 1 free",
    scheme_type: "free_goods",
    minimum_quantity: 10,
    free_quantity: 1,
    discount_percent: "0.00",
    starts_at: "2026-01-01T00:00:00Z",
    ends_at: null,
    reference: {
      quantity: 10,
      free_quantity: 1,
      subtotal: "7776.00",
      discount_amount: "0.00",
      illustrative_total: "7776.00",
    },
  },
};

const detergent: CatalogueProduct = {
  ...biscuits,
  id: "p-detergent",
  sku: "SKU-DT-1KG",
  name: "Detergent powder 1 kg",
  brand: "Sample Home Care",
  category: "Home Care",
  units_per_case: 20,
  stock_quantity: 85,
  unit_price: "2030.00",
  scheme: {
    ...biscuits.scheme!,
    id: "s-detergent",
    name: "3% off 5 or more cases",
    scheme_type: "slab_discount",
    minimum_quantity: 5,
    free_quantity: 0,
    discount_percent: "3.00",
    reference: null,
  },
};

const tea: CatalogueProduct = {
  ...biscuits,
  id: "p-tea",
  sku: "SKU-TE-250",
  name: "Tea leaf 250 g",
  brand: "Sample Beverages",
  category: "Beverages",
  units_per_case: 40,
  stock_quantity: 0,
  is_available: false,
  unit_price: "4720.00",
  scheme_status: "none",
  scheme: null,
};

function page(items: CatalogueProduct[], total = items.length, offset = 0) {
  return json(200, { items, total, limit: 24, offset });
}

const CATEGORIES = json(200, { categories: ["Beverages", "Biscuits", "Home Care"] });

function renderCatalogue(routes: Parameters<typeof mockApi>[0]) {
  const backend = mockApi({
    "GET /auth/me": json(200, retailerUser),
    "GET /retailer/catalogue/categories": CATEGORIES,
    ...routes,
  });
  const user = userEvent.setup();
  renderWithSession(<CatalogueScreen />);
  const catalogueCalls = () => backend.calls("GET /retailer/catalogue");
  const lastQuery = () => catalogueCalls().at(-1)?.query;
  return { backend, user, catalogueCalls, lastQuery };
}

function card(name: string) {
  return screen.getByRole("article", { name });
}

describe("CatalogueScreen", () => {
  it("shows a loading state, then the retailer's products from the API", async () => {
    const { lastQuery } = renderCatalogue({
      "GET /retailer/catalogue": page([biscuits, detergent, tea]),
    });

    expect(screen.getByText("Loading products…")).toBeInTheDocument();
    expect(await screen.findByRole("article", { name: "Glucose biscuit 75 g" })).toBeInTheDocument();

    expect(lastQuery()?.get("limit")).toBe("24");
    expect(lastQuery()?.get("offset")).toBe("0");
    expect(lastQuery()?.has("search")).toBe(false);
    expect(lastQuery()?.has("category")).toBe(false);
    expect(screen.getByText("3 products")).toBeInTheDocument();
  });

  it("shows SKU, category, pack size, price with GST treatment, stock and scheme", async () => {
    renderCatalogue({ "GET /retailer/catalogue": page([biscuits, detergent, tea]) });

    await screen.findByRole("article", { name: "Glucose biscuit 75 g" });
    const biscuitCard = within(card("Glucose biscuit 75 g"));
    expect(biscuitCard.getByText("SKU-BS-075")).toBeInTheDocument();
    expect(biscuitCard.getByText(/Sample Foods · Biscuits/)).toBeInTheDocument();
    expect(biscuitCard.getByText("₹777.60")).toBeInTheDocument();
    expect(biscuitCard.getByText(/Excludes GST · 96 units per case/)).toBeInTheDocument();
    expect(biscuitCard.getByText("In stock")).toBeInTheDocument();
    expect(biscuitCard.getByText("340 cases available")).toBeInTheDocument();
    expect(biscuitCard.getByText("Buy 10 cases, get 1 free")).toBeInTheDocument();
    expect(biscuitCard.getByText("Example at 10 cases")).toBeInTheDocument();
    // The exact price of one unit inside the case: 777.60 / 96.
    expect(biscuitCard.getByText("₹8.10")).toBeInTheDocument();
    expect(
      biscuitCard.getByText("For every 10 cases you order, you get 1 case free."),
    ).toBeInTheDocument();
    // A large, decorative picture, hidden from screen readers.
    expect(biscuitCard.getByTestId("product-image")).toHaveAttribute("aria-hidden", "true");
    expect(biscuitCard.getByText("₹7,776.00", { selector: "dd.font-semibold" })).toBeInTheDocument();

    const detergentCard = within(card("Detergent powder 1 kg"));
    expect(detergentCard.getByText("₹2,030.00")).toBeInTheDocument();
    expect(detergentCard.getByText("3% off on 5 or more cases")).toBeInTheDocument();
  });

  it("keeps out-of-stock products visible and clearly marked", async () => {
    renderCatalogue({ "GET /retailer/catalogue": page([tea]) });

    const teaCard = within(await screen.findByRole("article", { name: "Tea leaf 250 g" }));
    expect(teaCard.getByText("Out of stock")).toBeInTheDocument();
    expect(teaCard.queryByText("In stock")).not.toBeInTheDocument();
    expect(teaCard.queryByText(/Scheme/)).not.toBeInTheDocument();
  });

  it("flags a scheme configuration error instead of showing a scheme", async () => {
    renderCatalogue({
      "GET /retailer/catalogue": page([
        { ...detergent, scheme_status: "configuration_error", scheme: null },
      ]),
    });

    const detergentCard = within(
      await screen.findByRole("article", { name: "Detergent powder 1 kg" }),
    );
    expect(detergentCard.getByText("Scheme under review.")).toBeInTheDocument();
  });

  it("searches on the backend after typing pauses", async () => {
    const { user, lastQuery } = renderCatalogue({
      "GET /retailer/catalogue": (request: RecordedRequest) =>
        request.query.get("search") === "tea" ? page([tea])(request) : page([biscuits, tea])(request),
    });
    await screen.findByRole("article", { name: "Glucose biscuit 75 g" });

    await user.type(screen.getByRole("searchbox", { name: "Search products" }), "  tea ");

    await waitFor(() => expect(lastQuery()?.get("search")).toBe("tea"));
    await waitFor(() =>
      expect(screen.queryByRole("article", { name: "Glucose biscuit 75 g" })).not.toBeInTheDocument(),
    );
    expect(card("Tea leaf 250 g")).toBeInTheDocument();
  });

  it("filters by a category from the backend's list", async () => {
    const { user, lastQuery } = renderCatalogue({
      "GET /retailer/catalogue": (request: RecordedRequest) =>
        request.query.get("category") === "Beverages"
          ? page([tea])(request)
          : page([biscuits, tea])(request),
    });
    await screen.findByRole("article", { name: "Glucose biscuit 75 g" });

    const group = screen.getByRole("group", { name: "Filter by category" });
    const beverages = within(group).getByRole("button", { name: "Beverages" });
    await user.click(beverages);

    expect(beverages).toHaveAttribute("aria-pressed", "true");
    expect(within(group).getByRole("button", { name: "All" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    await waitFor(() => expect(lastQuery()?.get("category")).toBe("Beverages"));
    await waitFor(() =>
      expect(screen.queryByRole("article", { name: "Glucose biscuit 75 g" })).not.toBeInTheDocument(),
    );
  });

  it("explains an empty search and clears it", async () => {
    const { user, lastQuery } = renderCatalogue({
      "GET /retailer/catalogue": (request: RecordedRequest) =>
        request.query.has("search") ? page([])(request) : page([biscuits])(request),
    });
    await screen.findByRole("article", { name: "Glucose biscuit 75 g" });

    await user.type(screen.getByRole("searchbox", { name: "Search products" }), "xyz");
    expect(await screen.findByRole("heading", { name: "No products match" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Clear search and filters" }));

    expect(await screen.findByRole("article", { name: "Glucose biscuit 75 g" })).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Search products" })).toHaveValue("");
    expect(lastQuery()?.has("search")).toBe(false);
  });

  it("explains an empty catalogue without offering a dead action", async () => {
    renderCatalogue({ "GET /retailer/catalogue": page([]) });

    expect(
      await screen.findByRole("heading", { name: "No products available yet" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Clear search and filters" }),
    ).not.toBeInTheDocument();
  });

  it("shows an error with a working retry", async () => {
    const { user, catalogueCalls } = renderCatalogue({
      "GET /retailer/catalogue": sequence(
        json(500, { detail: "Internal Server Error" }),
        page([biscuits]),
      ),
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("Internal Server Error");
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("article", { name: "Glucose biscuit 75 g" })).toBeInTheDocument();
    expect(catalogueCalls()).toHaveLength(2);
  });

  it("loads further pages using the backend's offset and total", async () => {
    const { user, lastQuery } = renderCatalogue({
      "GET /retailer/catalogue": (request: RecordedRequest) =>
        request.query.get("offset") === "2"
          ? page([tea], 3, 2)(request)
          : page([biscuits, detergent], 3, 0)(request),
    });

    await screen.findByRole("article", { name: "Glucose biscuit 75 g" });
    expect(screen.getByText("Showing 2 of 3 products")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Show more products" }));

    expect(await screen.findByRole("article", { name: "Tea leaf 250 g" })).toBeInTheDocument();
    expect(lastQuery()?.get("offset")).toBe("2");
    expect(screen.queryByRole("button", { name: "Show more products" })).not.toBeInTheDocument();
    expect(screen.getByText("3 products")).toBeInTheDocument();
  });
});
