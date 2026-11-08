import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SESSION_QUERY_KEY } from "@/features/auth/api";
import { deskUser, json, mockApi, sequence } from "@/test/api-mock";
import {
  BATCH_ID,
  csvResponse,
  makeBatchDetail,
  withWebCrypto,
} from "@/test/export-fixtures";
import { renderWithSession } from "@/test/render";

import { saveFile } from "../save-file";
import { BatchScreen } from "./batch-screen";

vi.mock("../save-file", () => ({ saveFile: vi.fn() }));

const DETAIL = `GET /desk/exports/${BATCH_ID}`;
const CSV = `GET /desk/exports/${BATCH_ID}/csv`;

async function renderBatch(routes: Parameters<typeof mockApi>[0] = {}, batchId = BATCH_ID) {
  const backend = mockApi({
    "GET /auth/me": json(200, deskUser),
    [DETAIL]: json(200, makeBatchDetail()),
    [CSV]: csvResponse(),
    ...routes,
  });
  const user = userEvent.setup();
  const result = renderWithSession(<BatchScreen batchId={batchId} />);
  await waitFor(() =>
    expect(result.queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(deskUser),
  );
  return { ...result, backend, user };
}

beforeEach(() => {
  withWebCrypto();
});

describe("BatchScreen", () => {
  it("shows the batch, its checksum and its orders", async () => {
    await renderBatch();

    expect(
      await screen.findByRole("heading", { name: "Export EX-2026-000001" }),
    ).toBeInTheDocument();
    const summary = screen.getByRole("region", { name: "Batch summary" });
    const values = within(summary)
      .getAllByRole("definition")
      .map((value) => value.textContent);
    expect(values).toEqual([
      "7 Nov 2026, 3:30 pm IST",
      "Order Desk Operator",
      "2",
      "₹17,621.50 (excl. GST)",
      makeBatchDetail().csv_sha256,
    ]);
    expect(screen.getByText(/not a\s+verified native Tally import/)).toBeInTheDocument();

    const orders = screen.getByRole("region", { name: "Orders in this export" });
    const link = within(orders).getByRole("link", { name: "OB-2026-000123" });
    expect(link).toHaveAttribute("href", "/desk/orders/44444444-4444-4444-8444-444444444444");
    const row = link.closest("li")!;
    expect(row).toHaveTextContent("Sharma General Store");
    expect(row).toHaveTextContent("Confirmed 7 Nov 2026, 2:30 pm IST");
    expect(row).toHaveTextContent("Exported");
    expect(row).toHaveTextContent("₹7,776.00");
    expect(within(orders).getAllByRole("listitem")).toHaveLength(2);
    // The back link and the breadcrumb both lead to the export screen.
    const backLinks = screen.getAllByRole("link", { name: "Tally export" });
    expect(backLinks.length).toBeGreaterThan(0);
    for (const back of backLinks) {
      expect(back).toHaveAttribute("href", "/desk/exports");
    }
  });

  it("downloads the verified CSV", async () => {
    const { user } = await renderBatch();
    await screen.findByRole("heading", { name: "Export EX-2026-000001" });

    await user.click(screen.getByRole("button", { name: /^Download CSV\s+for EX-2026-000001$/ }));

    expect(await screen.findByText(/matches its SHA-256 checksum/)).toBeInTheDocument();
    expect(saveFile).toHaveBeenCalledTimes(1);
  });

  it("treats a malformed id as not found without a request", async () => {
    const { backend } = await renderBatch({}, "not-an-id");

    expect(screen.getByRole("heading", { name: "Export batch not found" })).toBeInTheDocument();
    expect(backend.requests.filter((r) => r.path.startsWith("/desk"))).toEqual([]);
  });

  it("shows not found for a 404", async () => {
    await renderBatch({ [DETAIL]: json(404, { detail: "Export batch not found." }) });

    expect(
      await screen.findByRole("heading", { name: "Export batch not found" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to Tally export" })).toHaveAttribute(
      "href",
      "/desk/exports",
    );
  });

  it("recovers from a load error", async () => {
    const { user } = await renderBatch({
      [DETAIL]: sequence(json(500, {}), json(200, makeBatchDetail())),
    });

    expect(await screen.findByText(/Something went wrong on our side/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(
      await screen.findByRole("heading", { name: "Export EX-2026-000001" }),
    ).toBeInTheDocument();
  });

  it("handles a missing creator and confirmation time", async () => {
    const detail = makeBatchDetail({ created_by_name: null });
    detail.orders = [{ ...detail.orders[0]!, confirmed_at: null }];
    await renderBatch({ [DETAIL]: json(200, detail) });

    const summary = await screen.findByRole("region", { name: "Batch summary" });
    expect(summary).toHaveTextContent("Not recorded");
    expect(screen.getByRole("region", { name: "Orders in this export" })).toHaveTextContent(
      "Confirmed time not recorded",
    );
  });
});
