import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

import { MainNav } from "@/components/layout/main-nav";
import { SESSION_QUERY_KEY } from "@/features/auth/api";
import { biscuits, makeProduct, tea } from "@/test/cart-fixtures";
import { json, mockApi, retailerUser } from "@/test/api-mock";
import { setPathname } from "@/test/navigation-mock";
import { renderWithSession } from "@/test/render";

import { AddToCart } from "./add-to-cart";

async function renderSignedIn(ui: ReactElement) {
  mockApi({ "GET /auth/me": json(200, retailerUser) });
  const result = renderWithSession(ui);
  await waitFor(() =>
    expect(result.queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(retailerUser),
  );
  return result;
}

describe("AddToCart", () => {
  it("cannot add an out-of-stock product", async () => {
    await renderSignedIn(<AddToCart product={tea} />);

    expect(screen.getByRole("button", { name: "Not available to order" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Add to cart/ })).not.toBeInTheDocument();
  });

  it("cannot add a product whose scheme is under review", async () => {
    await renderSignedIn(
      <AddToCart product={makeProduct({ scheme_status: "configuration_error" })} />,
    );

    expect(screen.getByRole("button", { name: "Cannot be ordered right now" })).toBeDisabled();
  });

  it("adds a product, then adjusts its quantity in its unit", async () => {
    const user = userEvent.setup();
    await renderSignedIn(<AddToCart product={biscuits} />);

    await user.click(screen.getByRole("button", { name: /Add to cart/ }));

    const quantity = screen.getByRole("textbox", {
      name: "Quantity of Glucose biscuit 75 g, in cases",
    });
    expect(quantity).toHaveValue("1");
    expect(screen.getByRole("button", { name: /Decrease quantity/ })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /Increase quantity/ }));
    expect(quantity).toHaveValue("2");
    expect(screen.getByText("cases")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Decrease quantity/ }));
    expect(quantity).toHaveValue("1");

    // Typed values are committed on Enter and kept within 1 to 10,000.
    await user.clear(quantity);
    await user.type(quantity, "20000{Enter}");
    expect(quantity).toHaveValue("10000");
    await user.clear(quantity);
    await user.type(quantity, "{Enter}");
    expect(quantity).toHaveValue("10000");
  });

  it("shows the number of products in the cart in the navigation", async () => {
    setPathname("/retailer");
    const user = userEvent.setup();
    await renderSignedIn(
      <>
        <MainNav />
        <AddToCart product={biscuits} />
      </>,
    );
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: "Cart, empty" })).toHaveAttribute(
      "href",
      "/retailer/cart",
    );

    await user.click(screen.getByRole("button", { name: /Add to cart/ }));

    expect(within(nav).getByRole("link", { name: "Cart, 1 product" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Catalogue" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});
