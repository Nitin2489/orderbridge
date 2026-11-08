import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { endClientSession, SESSION_QUERY_KEY } from "@/features/auth/api";
import { deskKeys } from "@/features/desk/api";
import {
  deferred,
  deskUser,
  json,
  mockApi,
  networkFailure,
  sequence,
} from "@/test/api-mock";
import {
  BATCH_ID,
  CSV_TEXT,
  NEW_BATCH_ID,
  batchPage,
  csvResponse,
  makeBatchSummary,
  savedText,
  withRandomValuesOnly,
  withWebCrypto,
  withoutSubtleCrypto,
} from "@/test/export-fixtures";
import { ORDER_ID, makeDeskDetail } from "@/test/desk-fixtures";
import { renderWithSession } from "@/test/render";

import { exportKeys } from "../api";
import { saveFile } from "../save-file";
import { ExportScreen } from "./export-screen";

vi.mock("../save-file", () => ({ saveFile: vi.fn() }));

const ELIGIBLE = "GET /desk/exports/eligible";
const HISTORY = "GET /desk/exports";
const CREATE = "POST /desk/exports";
const CSV = `GET /desk/exports/${BATCH_ID}/csv`;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const waiting = { order_count: 2, net_total: "17621.50" };
const existing = makeBatchSummary();
// Deliberately different from the waiting summary: the screen must show the
// backend's figures for the batch, not the earlier estimate.
const created = makeBatchSummary({
  id: NEW_BATCH_ID,
  batch_number: "EX-2026-000002",
  order_count: 3,
  net_total: "20000.00",
});

const otherDeskUser = { ...deskUser, id: "1f2e3d4c-5b6a-4978-8695-a4b3c2d1e0f9" };

type Routes = Parameters<typeof mockApi>[0];

async function renderExports(routes: Routes = {}) {
  const backend = mockApi({
    "GET /auth/me": json(200, deskUser),
    [ELIGIBLE]: json(200, waiting),
    [HISTORY]: json(200, batchPage([existing])),
    [CSV]: csvResponse(),
    ...routes,
  });
  const user = userEvent.setup();
  const result = renderWithSession(<ExportScreen />);
  await waitFor(() =>
    expect(result.queryClient.getQueryData(SESSION_QUERY_KEY)).toEqual(deskUser),
  );
  return { ...result, backend, user };
}

type Rendered = Awaited<ReturnType<typeof renderExports>>;

async function startExport(user: Rendered["user"]) {
  await user.click(await screen.findByRole("button", { name: "Export to CSV" }));
  await user.click(screen.getByRole("button", { name: /^(Yes, export orders|Retry export)$/ }));
}

function requestIds(backend: Rendered["backend"]): string[] {
  return backend
    .calls(CREATE)
    .map((request) => (request.body as { client_request_id: string }).client_request_id);
}

function historyRow(batchNumber: string) {
  const link = screen.getByRole("link", { name: batchNumber });
  return link.closest("li")!;
}

function downloadButton(container: HTMLElement, batchNumber: string) {
  return within(container).getByRole("button", {
    name: new RegExp(`^Download CSV(?: \\(unverified\\))?\\s+for ${batchNumber}$`),
  });
}

/** Lets any (wrongly) triggered refetch or render happen before asserting. */
async function settle(queryClient: Rendered["queryClient"]) {
  await waitFor(() => expect(queryClient.isMutating()).toBe(0));
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
}

beforeEach(() => {
  withWebCrypto();
});

describe("ExportScreen: waiting orders and history", () => {
  it("shows the waiting orders, what the file is, and earlier exports", async () => {
    await renderExports();

    const card = screen.getByRole("region", { name: "Waiting for export" });
    expect(await within(card).findByText("2 orders")).toBeInTheDocument();
    expect(card).toHaveTextContent("₹17,621.50");
    expect(card).toHaveTextContent("(excl. GST)");
    expect(screen.getByRole("heading", { name: "Tally export" })).toBeInTheDocument();
    expect(screen.getByText(/not a\s+verified native Tally import/)).toHaveTextContent(
      /ledgers, GST and voucher types must be mapped in Tally/,
    );

    const history = screen.getByRole("region", { name: "Export history" });
    const link = await within(history).findByRole("link", { name: "EX-2026-000001" });
    expect(link).toHaveAttribute("href", `/desk/exports/${BATCH_ID}`);
    const row = historyRow("EX-2026-000001");
    expect(row).toHaveTextContent("7 Nov 2026, 3:30 pm IST · Order Desk Operator");
    expect(row).toHaveTextContent("2 orders · ₹17,621.50 (excl. GST)");
    expect(within(history).getByText("1 export")).toBeInTheDocument();
  });

  it("offers no export when nothing is waiting, and says when there is no history", async () => {
    await renderExports({
      [ELIGIBLE]: json(200, { order_count: 0, net_total: "0.00" }),
      [HISTORY]: json(200, batchPage([])),
    });

    expect(
      await screen.findByText("No confirmed orders are waiting for export"),
    ).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "No exports yet" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Export to CSV" })).not.toBeInTheDocument();
  });

  it("recovers from load errors", async () => {
    const { user } = await renderExports({
      [ELIGIBLE]: sequence(json(500, {}), json(200, waiting)),
      [HISTORY]: sequence(networkFailure(), json(200, batchPage([existing]))),
    });

    const card = screen.getByRole("region", { name: "Waiting for export" });
    const history = screen.getByRole("region", { name: "Export history" });
    expect(await within(card).findByText(/Something went wrong on our side/)).toBeInTheDocument();
    expect(await within(history).findByText(/Unable to reach OrderBridge/)).toBeInTheDocument();

    await user.click(within(card).getByRole("button", { name: "Try again" }));
    await user.click(within(history).getByRole("button", { name: "Try again" }));

    expect(await within(card).findByText("2 orders")).toBeInTheDocument();
    expect(await within(history).findByRole("link", { name: "EX-2026-000001" })).toBeInTheDocument();
  });

  it("loads more history on request", async () => {
    const second = makeBatchSummary({
      id: "77777777-7777-4777-8777-777777777777",
      batch_number: "EX-2026-000000",
    });
    const { user, backend } = await renderExports({
      [HISTORY]: sequence(
        json(200, batchPage([existing], 2)),
        json(200, { items: [second], total: 2, limit: 20, offset: 1 }),
      ),
    });

    expect(await screen.findByText("Showing 1 of 2 exports")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show more exports" }));

    expect(await screen.findByRole("link", { name: "EX-2026-000000" })).toBeInTheDocument();
    expect(backend.calls(HISTORY).map((r) => r.query.get("offset"))).toEqual(["0", "1"]);
    expect(backend.calls(HISTORY)[0]?.query.get("limit")).toBe("20");
    expect(screen.queryByRole("button", { name: "Show more exports" })).not.toBeInTheDocument();
  });
});

describe("ExportScreen: creating an export", () => {
  it("asks first, explaining the status change, then sends only a request id", async () => {
    const { user, backend } = await renderExports({ [CREATE]: json(201, created) });

    await user.click(await screen.findByRole("button", { name: "Export to CSV" }));
    const heading = screen.getByRole("heading", { name: "Export all confirmed orders waiting now?" });
    expect(heading).toHaveFocus();
    const step = heading.closest("section")!;
    expect(step).toHaveTextContent("Currently waiting: 2 orders, ₹17,621.50 (excl. GST)");
    expect(step).toHaveTextContent("change from Confirmed to Exported");
    expect(step).toHaveTextContent("generic CSV, not a native Tally import");
    expect(backend.calls(CREATE)).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Yes, export orders" }));

    const result = await screen.findByText(/Export created: EX-2026-000002/);
    const banner = result.closest("[role=status]") as HTMLElement;
    // The batch's exact count and total, as the backend reported them.
    expect(banner).toHaveTextContent("3 orders exported · ₹20,000.00 (excl. GST)");
    expect(banner).toHaveTextContent("These orders are now Exported.");
    expect(within(banner).getByRole("link", { name: "View batch" })).toHaveAttribute(
      "href",
      `/desk/exports/${NEW_BATCH_ID}`,
    );
    expect(downloadButton(banner, "EX-2026-000002")).toBeInTheDocument();

    const [body] = backend.calls(CREATE).map((request) => request.body);
    expect(Object.keys(body as object)).toEqual(["client_request_id"]);
    expect(requestIds(backend)[0]).toMatch(UUID);
    expect(
      screen.queryByRole("heading", { name: "Export all confirmed orders waiting now?" }),
    ).not.toBeInTheDocument();
  });

  it("re-reads the waiting orders, history and desk orders after an export", async () => {
    const { user, backend, queryClient } = await renderExports({
      [CREATE]: json(201, created),
    });
    queryClient.setQueryData(deskKeys.detail(ORDER_ID), makeDeskDetail({ status: "confirmed" }));
    await screen.findByText("2 orders");

    await startExport(user);
    await screen.findByText(/Export created/);

    await waitFor(() => expect(backend.calls(ELIGIBLE)).toHaveLength(2));
    await waitFor(() => expect(backend.calls(HISTORY)).toHaveLength(2));
    expect(queryClient.getQueryState(deskKeys.detail(ORDER_ID))?.isInvalidated).toBe(true);
  });

  it("says when a repeated request returned the batch already created", async () => {
    const { user } = await renderExports({ [CREATE]: json(200, created) });

    await startExport(user);

    expect(
      await screen.findByText(/This export had already been created: EX-2026-000002/),
    ).toBeInTheDocument();
  });

  it("uses a new request id for each new export", async () => {
    const { user, backend } = await renderExports({ [CREATE]: json(201, created) });

    await startExport(user);
    await screen.findByText(/Export created/);
    await startExport(user);
    await waitFor(() => expect(backend.calls(CREATE)).toHaveLength(2));

    const [first, second] = requestIds(backend);
    expect(first).not.toBe(second);
  });

  it("reports that nothing was waiting, re-reads, and starts the next export afresh", async () => {
    const { user, backend } = await renderExports({
      [ELIGIBLE]: sequence(json(200, waiting), json(200, { order_count: 0, net_total: "0.00" })),
      [CREATE]: json(422, { detail: "No confirmed orders are waiting for export." }),
    });

    await startExport(user);

    expect(
      await screen.findByText("No confirmed orders are waiting for export. Nothing was exported."),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("No confirmed orders are waiting for export"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry export" })).not.toBeInTheDocument();
    expect(backend.calls(CREATE)).toHaveLength(1);
  });

  it("treats a conflict as definite: nothing exported, a new id next time", async () => {
    const { user, backend } = await renderExports({
      [CREATE]: sequence(json(409, { detail: "Conflict." }), json(201, created)),
    });

    await startExport(user);
    expect(await screen.findByText(/nothing was exported/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry export" })).not.toBeInTheDocument();

    await startExport(user);
    await screen.findByText(/Export created/);
    const [first, second] = requestIds(backend);
    expect(first).not.toBe(second);
  });

  it("after an uncertain outcome, retries only on request, with the same request id", async () => {
    const { user, backend } = await renderExports({
      [CREATE]: sequence(networkFailure(), json(200, created)),
    });

    await startExport(user);

    expect(
      await screen.findByText(/could not tell whether the export was created/),
    ).toBeInTheDocument();
    // The waiting summary and history are re-read; the export is not resent.
    await waitFor(() => expect(backend.calls(ELIGIBLE)).toHaveLength(2));
    expect(backend.calls(CREATE)).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "Retry export" }));

    // The backend recognised the id: the batch the first attempt created.
    expect(
      await screen.findByText(/This export had already been created: EX-2026-000002/),
    ).toBeInTheDocument();
    const [first, second] = requestIds(backend);
    expect(second).toBe(first);
  });

  it("treats a 5xx as uncertain, and a cancel discards the request id", async () => {
    const { user, backend } = await renderExports({
      [CREATE]: sequence(json(502, { detail: "Bad gateway" }), json(201, created)),
    });

    await startExport(user);
    expect(await screen.findByRole("button", { name: "Retry export" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText(/could not tell whether/)).not.toBeInTheDocument();

    await startExport(user);
    await screen.findByText(/Export created/);
    const [first, second] = requestIds(backend);
    expect(second).not.toBe(first);
  });

  it("sends one request when confirmed repeatedly", async () => {
    const slow = deferred(json(201, created));
    const { user, backend } = await renderExports({ [CREATE]: slow.handler });

    await startExport(user);
    const busy = screen.getByRole("button", { name: /Exporting/ });
    expect(busy).toBeDisabled();
    await user.click(busy);
    expect(backend.calls(CREATE)).toHaveLength(1);

    act(() => slow.resolve());
    await screen.findByText(/Export created/);
    expect(backend.calls(CREATE)).toHaveLength(1);
  });

  it("works without crypto.randomUUID (insecure context)", async () => {
    withRandomValuesOnly();
    const { user, backend } = await renderExports({ [CREATE]: json(201, created) });

    await startExport(user);

    await screen.findByText(/Export created/);
    expect(requestIds(backend)[0]).toMatch(UUID);
  });

  it("exports nothing when the browser cannot create a request id", async () => {
    vi.stubGlobal("crypto", undefined);
    const { user, backend } = await renderExports();

    await startExport(user);

    expect(await screen.findByText(/cannot create a safe request id/)).toBeInTheDocument();
    expect(backend.calls(CREATE)).toHaveLength(0);
  });
});

describe("ExportScreen: downloads", () => {
  it("saves the CSV only after its checksum matches", async () => {
    const { user, backend } = await renderExports();
    await screen.findByRole("link", { name: "EX-2026-000001" });
    const row = historyRow("EX-2026-000001");

    await user.click(downloadButton(row, "EX-2026-000001"));

    expect(await within(row).findByText(/matches its SHA-256 checksum/)).toBeInTheDocument();
    expect(saveFile).toHaveBeenCalledTimes(1);
    const [bytes, filename, type] = vi.mocked(saveFile).mock.calls[0]!;
    expect(savedText(bytes)).toBe(CSV_TEXT);
    expect(filename).toBe("orderbridge-tally-EX-2026-000001.csv");
    expect(type).toBe("text/csv;charset=utf-8");
    expect(backend.calls(CSV)).toHaveLength(1);
  });

  it("does not save a file whose checksum does not match", async () => {
    const { user } = await renderExports({
      [CSV]: csvResponse(CSV_TEXT.replace("7776.00", "1.00")),
    });
    await screen.findByRole("link", { name: "EX-2026-000001" });
    const row = historyRow("EX-2026-000001");

    await user.click(downloadButton(row, "EX-2026-000001"));

    expect(await within(row).findByRole("alert")).toHaveTextContent(
      /did not match the checksum recorded for this export, so it was not saved/,
    );
    expect(saveFile).not.toHaveBeenCalled();
  });

  it("without Web Crypto, says before and after that the file is unverified", async () => {
    withoutSubtleCrypto();
    const { user } = await renderExports();
    await screen.findByRole("link", { name: "EX-2026-000001" });
    const row = historyRow("EX-2026-000001");

    const button = downloadButton(row, "EX-2026-000001");
    expect(button).toHaveTextContent("Download CSV (unverified)");
    expect(row).toHaveTextContent("This browser cannot verify downloads");

    await user.click(button);

    expect(
      await within(row).findByText(/Downloaded without verification/),
    ).toBeInTheDocument();
    expect(saveFile).toHaveBeenCalledTimes(1);
  });

  it("explains a failed download and saves nothing", async () => {
    const { user } = await renderExports({
      [CSV]: json(404, { detail: "Export batch not found." }),
    });
    await screen.findByRole("link", { name: "EX-2026-000001" });
    const row = historyRow("EX-2026-000001");

    await user.click(downloadButton(row, "EX-2026-000001"));

    expect(await within(row).findByRole("alert")).toHaveTextContent(
      "This export batch was not found.",
    );
    expect(saveFile).not.toHaveBeenCalled();
  });

  it("does not save a file that arrives after another user signed in", async () => {
    const slow = deferred(csvResponse());
    const { user, queryClient } = await renderExports({ [CSV]: slow.handler });
    await screen.findByRole("link", { name: "EX-2026-000001" });

    await user.click(downloadButton(historyRow("EX-2026-000001"), "EX-2026-000001"));
    act(() => {
      queryClient.setQueryData(SESSION_QUERY_KEY, otherDeskUser);
    });
    act(() => slow.resolve());
    await settle(queryClient);

    expect(saveFile).not.toHaveBeenCalled();
    expect(screen.queryByText(/Downloaded/)).not.toBeInTheDocument();
  });
});

describe("ExportScreen: a session change while an export is in flight", () => {
  it("ignores an export that succeeds after another user signed in", async () => {
    const slow = deferred(json(201, created));
    const { user, queryClient } = await renderExports({ [CREATE]: slow.handler });
    queryClient.setQueryData(deskKeys.detail(ORDER_ID), makeDeskDetail({ status: "confirmed" }));

    await startExport(user);
    act(() => {
      queryClient.setQueryData(SESSION_QUERY_KEY, otherDeskUser);
    });
    // The new user's screen starts afresh: no half-finished export step.
    expect(await screen.findByRole("button", { name: "Export to CSV" })).toBeInTheDocument();

    act(() => slow.resolve());
    await settle(queryClient);

    expect(screen.queryByText(/Export created/)).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(queryClient.getQueryState(deskKeys.detail(ORDER_ID))?.isInvalidated).toBe(false);
  });

  it("does not offer a retry for an uncertain export from the old session", async () => {
    const slow = deferred(networkFailure());
    const { user, queryClient, backend } = await renderExports({ [CREATE]: slow.handler });
    queryClient.setQueryData(deskKeys.detail(ORDER_ID), makeDeskDetail({ status: "confirmed" }));

    await startExport(user);
    act(() => {
      queryClient.setQueryData(SESSION_QUERY_KEY, otherDeskUser);
    });
    act(() => slow.resolve());
    await settle(queryClient);

    expect(screen.queryByText(/could not tell whether/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry export" })).not.toBeInTheDocument();
    expect(queryClient.getQueryState(deskKeys.detail(ORDER_ID))?.isInvalidated).toBe(false);
    expect(backend.calls(CREATE)).toHaveLength(1);
  });

  it("ignores an export that resolves after sign-out", async () => {
    const slow = deferred(json(201, created));
    const { user, queryClient } = await renderExports({ [CREATE]: slow.handler });

    await startExport(user);
    await act(async () => {
      await endClientSession(queryClient);
    });
    act(() => slow.resolve());
    await settle(queryClient);

    expect(queryClient.getQueryData(SESSION_QUERY_KEY)).toBeNull();
    expect(screen.queryByText(/Export created/)).not.toBeInTheDocument();
    expect(queryClient.getQueryData(exportKeys.history())).toBeUndefined();
  });
});
