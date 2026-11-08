import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SESSION_QUERY_KEY } from "@/features/auth/api";
import { biscuits } from "@/test/cart-fixtures";
import { deskUser, json, mockApi, retailerUser } from "@/test/api-mock";
import { renderWithSession } from "@/test/render";

import { useCart } from "./cart-provider";

const otherRetailer = {
  ...retailerUser,
  id: "8b2c3d4e-5f6a-4b7c-8d9e-555555555555",
  phone_number: "+919000000002",
  full_name: "Ravi Gupta",
  retailer: {
    id: "9c3d4e5f-6a7b-4c8d-9e0f-666666666666",
    business_name: "Gupta Kirana Store",
    owner_name: "Ravi Gupta",
    address: "Wright Town, Jabalpur",
  },
};

function CartProbe() {
  const cart = useCart();
  return (
    <>
      <p>Lines: {cart.itemCount}</p>
      <button type="button" onClick={() => cart.add(biscuits)}>
        Add biscuits
      </button>
    </>
  );
}

async function renderWithCartItem() {
  mockApi({ "GET /auth/me": json(200, retailerUser) });
  const user = userEvent.setup();
  const result = renderWithSession(<CartProbe />);
  await waitFor(() =>
    expect(result.queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(retailerUser),
  );
  await user.click(screen.getByRole("button", { name: "Add biscuits" }));
  expect(screen.getByText("Lines: 1")).toBeInTheDocument();
  return result;
}

describe("cart isolation", () => {
  it("empties the cart when another retailer signs in", async () => {
    const { queryClient } = await renderWithCartItem();

    act(() => queryClient.setQueryData(SESSION_QUERY_KEY, otherRetailer));

    expect(await screen.findByText("Lines: 0")).toBeInTheDocument();
  });

  it("empties the cart on sign-out and does not bring it back on sign-in", async () => {
    const { queryClient } = await renderWithCartItem();

    act(() => queryClient.setQueryData(SESSION_QUERY_KEY, null));
    expect(await screen.findByText("Lines: 0")).toBeInTheDocument();

    act(() => queryClient.setQueryData(SESSION_QUERY_KEY, retailerUser));
    expect(await screen.findByText("Lines: 0")).toBeInTheDocument();
  });

  it("has no cart for a non-retailer user", async () => {
    mockApi({ "GET /auth/me": json(200, deskUser) });
    const user = userEvent.setup();
    const { queryClient } = renderWithSession(<CartProbe />);
    await waitFor(() =>
      expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(deskUser),
    );

    await user.click(screen.getByRole("button", { name: "Add biscuits" }));

    // Nothing is added without a signed-in retailer.
    expect(screen.getByText("Lines: 0")).toBeInTheDocument();
  });
});
