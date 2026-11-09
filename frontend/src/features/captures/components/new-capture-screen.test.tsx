import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SESSION_QUERY_KEY } from "@/features/auth/api";
import { deskUser, json, mockApi, networkFailure, retailerUser, sequence } from "@/test/api-mock";
import { CAPTURE_ID, DEMO, SHARMA_ID, makeCapture } from "@/test/capture-fixtures";
import { mockRouter } from "@/test/navigation-mock";
import { renderWithSession } from "@/test/render";

import { NewCaptureScreen } from "./new-capture-screen";

const CREATE = "POST /captures";
const DIRECTORY = "GET /desk/retailers";

async function renderNew(routes: Parameters<typeof mockApi>[0] = {}, desk = true) {
  const user = desk ? deskUser : retailerUser;
  const backend = mockApi({
    "GET /auth/me": json(200, user),
    [DIRECTORY]: json(200, {
      items: [
        { id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", business_name: "Gupta Kirana Store", is_active: true },
        { id: SHARMA_ID, business_name: "Sharma General Store", is_active: true },
      ],
    }),
    ...routes,
  });
  const result = renderWithSession(<NewCaptureScreen role={desk ? "order_desk" : "retailer"} />);
  await waitFor(() => expect(result.queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(user));
  return { backend, user: userEvent.setup() };
}

describe("NewCaptureScreen", () => {
  it("the desk must choose the retailer and paste a message", async () => {
    const { user, backend } = await renderNew();

    await user.click(screen.getByRole("button", { name: "Read message" }));

    expect(screen.getByText("Choose the retailer this order is for.")).toBeInTheDocument();
    expect(screen.getByText("Paste or type the order as received.")).toBeInTheDocument();
    expect(backend.calls(CREATE)).toHaveLength(0);
  });

  it("uses desk wording that does not assume the retailer sent the message", async () => {
    await renderNew();

    expect(screen.getByLabelText("Retailer this order is for")).toBeInTheDocument();
    expect(
      screen.getByText(/as received: from WhatsApp, a phone call, a salesman's notebook or anywhere else/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Retailer who sent it/)).not.toBeInTheDocument();
  });

  it("uses retailer wording for a retailer's own message", async () => {
    const { user } = await renderNew({}, false);

    expect(screen.getByText(/Paste your order message exactly as you wrote it/)).toBeInTheDocument();
    expect(screen.queryByText(/salesman's notebook/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Read message" }));
    expect(screen.getByText("Paste your order message.")).toBeInTheDocument();
  });

  it("warns about a possible duplicate, can go back, or creates it anyway with the same request", async () => {
    const duplicate = json(409, {
      detail:
        "This retailer already has a capture with the same message from the last 24 hours. Check it, or create this one anyway if it is a separate order.",
      code: "possible_duplicate",
      duplicates: [
        {
          capture_id: CAPTURE_ID,
          created_at: "2026-11-09T04:00:00Z",
          status: "submitted",
          order: { id: "44444444-4444-4444-8444-444444444444", order_number: "OB-2026-000777", status: "pending_review" },
        },
      ],
    });
    const { user, backend } = await renderNew({
      [CREATE]: sequence(duplicate, duplicate, json(201, makeCapture())),
    });

    await user.click(await screen.findByRole("button", { name: "Sharma General Store" }));
    await user.type(screen.getByLabelText("Message"), DEMO);
    await user.click(screen.getByRole("button", { name: "Read message" }));

    const warning = await screen.findByRole("alert");
    expect(warning).toHaveTextContent("Possible duplicate");
    expect(warning).toHaveTextContent("Sharma General Store already has a capture with the same message");
    expect(warning).toHaveTextContent("Order OB-2026-000777 (Pending review)");
    expect(screen.getByRole("link", { name: /Captured 9 Nov 2026/ })).toHaveAttribute(
      "href",
      `/desk/captures/${CAPTURE_ID}`,
    );
    expect(mockRouter.push).not.toHaveBeenCalled();

    // Go back: the warning closes and nothing more is sent.
    await user.click(screen.getByRole("button", { name: "Go back and review" }));
    expect(screen.queryByText("Possible duplicate")).not.toBeInTheDocument();
    expect(backend.calls(CREATE)).toHaveLength(1);

    // Read it again: warned again; create anyway with the same request id.
    await user.click(screen.getByRole("button", { name: "Read message" }));
    await user.click(await screen.findByRole("button", { name: "Create anyway" }));

    await waitFor(() => expect(mockRouter.push).toHaveBeenCalledWith(`/desk/captures/${CAPTURE_ID}`));
    const calls = backend.calls(CREATE).map((call) => call.body as Record<string, unknown>);
    expect(calls).toHaveLength(3);
    expect(calls[0]).not.toHaveProperty("allow_duplicate");
    expect(calls[2]?.allow_duplicate).toBe(true);
    expect(calls[2]?.client_request_id).toBe(calls[1]?.client_request_id);
  });

  it("sends the message exactly as typed, with the chosen retailer, then opens the review", async () => {
    const { user, backend } = await renderNew({ [CREATE]: json(201, makeCapture()) });

    await user.click(await screen.findByRole("button", { name: "Sharma General Store" }));
    await user.type(screen.getByLabelText("Message"), `  ${DEMO}  `);
    await user.click(screen.getByRole("button", { name: "Read message" }));

    await waitFor(() => expect(mockRouter.push).toHaveBeenCalledWith(`/desk/captures/${CAPTURE_ID}`));
    const body = backend.calls(CREATE)[0]?.body as Record<string, string>;
    expect(body.message).toBe(`  ${DEMO}  `);
    expect(body.retailer_id).toBe(SHARMA_ID);
  });

  it("a retailer never names a retailer", async () => {
    const { user, backend } = await renderNew({ [CREATE]: json(201, makeCapture()) }, false);

    expect(screen.queryByText("Retailer this order is for")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Message"), DEMO);
    await user.click(screen.getByRole("button", { name: "Read message" }));

    await waitFor(() => expect(backend.calls(CREATE)).toHaveLength(1));
    expect(backend.calls(CREATE)[0]?.body).not.toHaveProperty("retailer_id");
    expect(backend.requests.some((r) => r.path === "/desk/retailers")).toBe(false);
  });

  it("explains a message with no order lines", async () => {
    const { user } = await renderNew(
      { [CREATE]: json(422, { detail: "No order lines were found in this message." }) },
      false,
    );

    await user.type(screen.getByLabelText("Message"), "bhaiya namaste");
    await user.click(screen.getByRole("button", { name: "Read message" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("No order lines were found");
  });

  it("retries an uncertain request with the same request id", async () => {
    const { user, backend } = await renderNew(
      { [CREATE]: sequence(networkFailure(), json(201, makeCapture())) },
      false,
    );

    await user.type(screen.getByLabelText("Message"), DEMO);
    await user.click(screen.getByRole("button", { name: "Read message" }));
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Read message" }));

    await waitFor(() => expect(backend.calls(CREATE)).toHaveLength(2));
    const [first, second] = backend.calls(CREATE).map(
      (request) => (request.body as { client_request_id: string }).client_request_id,
    );
    expect(second).toBe(first);
  });
});
