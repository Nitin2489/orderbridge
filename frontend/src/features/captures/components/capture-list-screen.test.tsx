import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SESSION_QUERY_KEY } from "@/features/auth/api";
import { deskUser, json, mockApi } from "@/test/api-mock";
import { CAPTURE_ID, makeSummary } from "@/test/capture-fixtures";
import { renderWithSession } from "@/test/render";

import { CaptureListScreen } from "./capture-list-screen";

const LIST = "GET /captures";

async function renderList(page: unknown) {
  const backend = mockApi({ "GET /auth/me": json(200, deskUser), [LIST]: json(200, page) });
  const result = renderWithSession(<CaptureListScreen role="order_desk" />);
  await waitFor(() => expect(result.queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(deskUser));
  return { backend, user: userEvent.setup() };
}

describe("CaptureListScreen", () => {
  it("lists messages that need review by default", async () => {
    const { backend } = await renderList({ items: [makeSummary()], total: 1, limit: 20, offset: 0 });

    const row = await screen.findByRole("link", { name: /Sharma General Store/ });
    expect(row).toHaveAttribute("href", `/desk/captures/${CAPTURE_ID}`);
    expect(row).toHaveTextContent("Needs review");
    expect(row).toHaveTextContent("3 lines · 3 to decide");
    expect(backend.calls(LIST)[0]?.query.get("status")).toBe("needs_review");
    expect(screen.getByText(/not an AI model/)).toBeInTheDocument();
  });

  it("filters by status, including discarded captures", async () => {
    const { user, backend } = await renderList({ items: [], total: 0, limit: 20, offset: 0 });

    await user.click(screen.getByRole("button", { name: "Discarded" }));

    await waitFor(() =>
      expect(backend.calls(LIST).map((r) => r.query.get("status"))).toContain("discarded"),
    );
    expect(await screen.findByRole("heading", { name: "No messages: discarded" })).toBeInTheDocument();
  });
});
