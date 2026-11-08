import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SESSION_QUERY_KEY } from "@/features/auth/api";
import { useCart } from "@/features/cart/cart-provider";
import { biscuits, detergent, makeProduct } from "@/test/cart-fixtures";
import { deskUser, json, mockApi, retailerUser } from "@/test/api-mock";
import { setPathname } from "@/test/navigation-mock";
import { renderWithSession } from "@/test/render";

import { MainNav } from "./main-nav";
import { MobileNav } from "./mobile-nav";
import { cartNavigationLabel } from "./use-primary-navigation";

const salt = makeProduct({ id: "66666666-6666-4666-8666-666666666666", name: "Salt 1 kg" });

function AddProducts() {
  const cart = useCart();
  return (
    <>
      <button type="button" onClick={() => cart.add(biscuits)}>
        Add one
      </button>
      <button
        type="button"
        onClick={() => {
          cart.add(detergent);
          cart.add(salt);
        }}
      >
        Add two more
      </button>
    </>
  );
}

async function renderNavigation() {
  setPathname("/retailer");
  mockApi({ "GET /auth/me": json(200, retailerUser) });
  const result = renderWithSession(
    <>
      <MainNav />
      <MobileNav />
      <AddProducts />
    </>,
  );
  await waitFor(() =>
    expect(result.queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(retailerUser),
  );
  return { user: userEvent.setup() };
}

function desktopNav() {
  return screen.getAllByRole("navigation", { name: "Primary" })[0]!;
}

describe("cartNavigationLabel", () => {
  it.each([
    [0, "Cart, empty"],
    [1, "Cart, 1 product"],
    [2, "Cart, 2 products"],
    [50, "Cart, 50 products"],
  ])("names a cart with %i products %j", (count, expected) => {
    expect(cartNavigationLabel(count)).toBe(expected);
  });
});

describe("cart link in the navigation", () => {
  it("says the cart is empty, with no badge", async () => {
    await renderNavigation();

    const link = within(desktopNav()).getByRole("link", { name: "Cart, empty" });
    expect(link).toHaveAttribute("href", "/retailer/cart");
    expect(link).toHaveTextContent(/^Cart$/);
  });

  it("states one and then several products, with the visible badge", async () => {
    const { user } = await renderNavigation();

    await user.click(screen.getByRole("button", { name: "Add one" }));
    const one = within(desktopNav()).getByRole("link", { name: "Cart, 1 product" });
    expect(one).toHaveTextContent("Cart1");

    await user.click(screen.getByRole("button", { name: "Add two more" }));
    const three = within(desktopNav()).getByRole("link", { name: "Cart, 3 products" });
    expect(three).toHaveTextContent("Cart3");
  });

  it("uses the same name in the mobile navigation", async () => {
    const { user } = await renderNavigation();
    await user.click(screen.getByRole("button", { name: "Add one" }));

    await user.click(screen.getByRole("button", { name: "Open navigation menu" }));

    const panel = await screen.findByRole("dialog");
    expect(within(panel).getByRole("link", { name: "Cart, 1 product" })).toHaveAttribute(
      "href",
      "/retailer/cart",
    );
  });

  it("offers Orders to retailers, active on order pages", async () => {
    setPathname("/retailer/orders/44444444-4444-4444-8444-444444444444");
    mockApi({ "GET /auth/me": json(200, retailerUser) });
    const { queryClient } = renderWithSession(<MainNav />);
    await waitFor(() =>
      expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(retailerUser),
    );

    const orders = screen.getByRole("link", { name: "Orders" });
    expect(orders).toHaveAttribute("href", "/retailer/orders");
    expect(orders).toHaveAttribute("aria-current", "page");
  });

  it("offers no retailer links to the order desk", async () => {
    mockApi({ "GET /auth/me": json(200, deskUser) });
    const { queryClient } = renderWithSession(<MainNav />);
    await waitFor(() => expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(deskUser));

    for (const name of ["Orders", "Catalogue", /Cart/]) {
      expect(screen.queryByRole("link", { name })).not.toBeInTheDocument();
    }
  });

  it("leaves other links named by their labels", async () => {
    await renderNavigation();

    expect(within(desktopNav()).getByRole("link", { name: "Catalogue" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(desktopNav()).getByRole("link", { name: "Overview" })).toBeInTheDocument();
  });
});
