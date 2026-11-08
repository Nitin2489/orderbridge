import { useQuery } from "@tanstack/react-query";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SESSION_QUERY_KEY } from "@/features/auth/api";
import { api } from "@/lib/api";
import {
  deskUser,
  json,
  mockApi,
  noContent,
  NOT_AUTHENTICATED,
  retailerUser,
  sequence,
} from "@/test/api-mock";
import { mockRouter, setPathname } from "@/test/navigation-mock";
import { renderWithSession } from "@/test/render";

import { RoleGate } from "./role-gate";
import { WorkspaceHome } from "./workspace-home";

function RetailerArea() {
  return (
    <RoleGate role="retailer">
      <p>Retailer content</p>
    </RoleGate>
  );
}

describe("RoleGate: session recovery and redirects", () => {
  it("recovers the session from /auth/me and renders the workspace", async () => {
    const backend = mockApi({ "GET /auth/me": json(200, retailerUser) });
    renderWithSession(<RetailerArea />);

    expect(screen.getByRole("status")).toHaveTextContent("Checking your session");
    expect(await screen.findByText("Retailer content")).toBeInTheDocument();
    expect(backend.calls("GET /auth/me")).toHaveLength(1);
    expect(mockRouter.replace).not.toHaveBeenCalled();
  });

  it("sends a signed-out visitor to sign-in without rendering the workspace", async () => {
    mockApi({ "GET /auth/me": NOT_AUTHENTICATED });
    renderWithSession(<RetailerArea />);

    await waitFor(() => expect(mockRouter.replace).toHaveBeenCalledWith("/sign-in"));
    expect(screen.queryByText("Retailer content")).not.toBeInTheDocument();
  });

  it("blocks another role and links to that user's own workspace", async () => {
    mockApi({ "GET /auth/me": json(200, deskUser) });
    renderWithSession(<RetailerArea />);

    expect(
      await screen.findByRole("heading", { name: "You do not have access to this page" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Retailer content")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /order desk workspace/i })).toHaveAttribute(
      "href",
      "/desk",
    );
  });

  it("blocks a retailer from the order desk", async () => {
    mockApi({ "GET /auth/me": json(200, retailerUser) });
    renderWithSession(
      <RoleGate role="order_desk">
        <p>Desk content</p>
      </RoleGate>,
    );

    expect(
      await screen.findByRole("heading", { name: "You do not have access to this page" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Desk content")).not.toBeInTheDocument();
  });

  it("denies an unknown role with no workspace to offer", async () => {
    mockApi({ "GET /auth/me": json(200, { ...retailerUser, role: "salesman" }) });
    renderWithSession(<RetailerArea />);

    expect(
      await screen.findByRole("heading", { name: "You do not have access to this page" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.queryByText("Retailer content")).not.toBeInTheDocument();
  });

  it("shows a retry when the session cannot be checked, without signing out", async () => {
    const backend = mockApi({
      "GET /auth/me": sequence(json(503, { detail: "Service unavailable" }), json(200, retailerUser)),
    });
    const user = userEvent.setup();
    renderWithSession(<RetailerArea />);

    await user.click(await screen.findByRole("button", { name: "Try again" }));

    expect(await screen.findByText("Retailer content")).toBeInTheDocument();
    expect(backend.calls("GET /auth/me")).toHaveLength(2);
    expect(mockRouter.replace).not.toHaveBeenCalled();
  });
});

describe("session expiry", () => {
  function Orders() {
    const orders = useQuery({
      queryKey: ["retailer", "orders"],
      queryFn: () => api.get<unknown>("/retailer/orders"),
    });
    return <p>{orders.isError ? "Orders failed" : "Orders"}</p>;
  }

  it("ends the client session when a protected request returns 401", async () => {
    mockApi({
      "GET /auth/me": json(200, retailerUser),
      "GET /retailer/orders": NOT_AUTHENTICATED,
    });
    const { queryClient } = renderWithSession(
      <RoleGate role="retailer">
        <Orders />
      </RoleGate>,
    );

    await waitFor(() => expect(mockRouter.replace).toHaveBeenCalledWith("/sign-in"));
    expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toBeNull();
    expect(queryClient.getQueryData(["retailer", "orders"])).toBeUndefined();
  });

  it("keeps the session when a protected request is forbidden (403)", async () => {
    mockApi({
      "GET /auth/me": json(200, retailerUser),
      "GET /retailer/orders": json(403, {
        detail: "You do not have permission to perform this action.",
      }),
    });
    const { queryClient } = renderWithSession(
      <RoleGate role="retailer">
        <Orders />
      </RoleGate>,
    );

    expect(await screen.findByText("Orders failed")).toBeInTheDocument();
    expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(retailerUser);
    expect(mockRouter.replace).not.toHaveBeenCalled();
  });
});

describe("sign out", () => {
  function renderWorkspace() {
    setPathname("/retailer");
    const result = renderWithSession(
      <RoleGate role="retailer">
        <WorkspaceHome />
      </RoleGate>,
    );
    // Stand-in for data a workspace screen would have cached.
    result.queryClient.setQueryData(["retailer", "catalogue"], { items: ["private"] });
    return result;
  }

  it("shows who is signed in, from the backend session", async () => {
    mockApi({ "GET /auth/me": json(200, retailerUser) });
    renderWorkspace();

    expect(
      await screen.findByRole("heading", { name: "Sharma General Store" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Retailer workspace")).toBeInTheDocument();
    expect(screen.getByText("+91 90000 00001")).toBeInTheDocument();
  });

  it("ends the session on the backend, clears cached data and goes to sign-in", async () => {
    const backend = mockApi({
      "GET /auth/me": json(200, retailerUser),
      "POST /auth/logout": noContent(),
    });
    const user = userEvent.setup();
    const { queryClient } = renderWorkspace();

    await user.click(await screen.findByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(mockRouter.replace).toHaveBeenCalledWith("/sign-in"));
    expect(backend.calls("POST /auth/logout")).toHaveLength(1);
    expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toBeNull();
    expect(queryClient.getQueryData(["retailer", "catalogue"])).toBeUndefined();
  });

  it("reports a failed sign-out and keeps the session as it is", async () => {
    mockApi({
      "GET /auth/me": json(200, retailerUser),
      "POST /auth/logout": json(500, { detail: "Internal Server Error" }),
    });
    const user = userEvent.setup();
    const { queryClient } = renderWorkspace();

    await user.click(await screen.findByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not sign out. Try again.");
    expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(retailerUser);
    expect(mockRouter.replace).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeEnabled();
  });
});
