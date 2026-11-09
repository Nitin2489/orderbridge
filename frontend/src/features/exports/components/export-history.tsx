"use client";

import { AlertCircleIcon, FileSpreadsheetIcon } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { formatCount, formatIndiaDateTime, formatRupees } from "@/features/catalogue/format";
import { isApiError } from "@/lib/api";

import { useExportHistory } from "../api";
import { exportBatchPath } from "../paths";
import type { ExportBatchSummary } from "../types";
import { DownloadButton } from "./download-button";

export function loadErrorMessage(error: unknown, what: string): string {
  if (isApiError(error) && error.status === 0) {
    return error.message;
  }
  if (isApiError(error) && error.status >= 500) {
    return "Something went wrong on our side. Please try again.";
  }
  return `${what} could not be loaded.`;
}

function HistoryRow({ batch }: { batch: ExportBatchSummary }) {
  const created = formatIndiaDateTime(batch.created_at) ?? "Time not available";
  return (
    <li className="flex flex-col gap-3 rounded-xl border bg-card p-5 text-card-foreground shadow-xs sm:flex-row sm:items-center sm:justify-between">
      <div className="flex flex-col gap-1">
        <Link
          href={exportBatchPath(batch.id)}
          className="tap-target w-fit rounded-sm font-mono font-semibold text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          {batch.batch_number}
        </Link>
        <span className="text-sm text-muted-foreground">
          {created}
          {batch.created_by_name ? ` · ${batch.created_by_name}` : ""}
        </span>
        <span className="text-sm">
          {formatCount(batch.order_count)} {batch.order_count === 1 ? "order" : "orders"} ·{" "}
          <span className="font-semibold tabular-nums">{formatRupees(batch.net_total)}</span>{" "}
          <span className="text-muted-foreground">(excl. GST)</span>
        </span>
      </div>
      <DownloadButton batch={batch} className="sm:items-end sm:text-right" />
    </li>
  );
}

/** Earlier export batches, newest first, each downloadable again. */
export function ExportHistory() {
  const history = useExportHistory();
  const pages = history.data?.pages ?? [];
  const items = pages.flatMap((page) => page.items);
  const total = pages[0]?.total ?? 0;

  let body: ReactNode;
  if (history.isPending) {
    body = (
      <>
        <p role="status" className="sr-only">
          Loading export history…
        </p>
        <ul aria-hidden="true" className="flex flex-col gap-3">
          {Array.from({ length: 3 }, (_, index) => (
            <li key={index} className="flex flex-col gap-2 rounded-xl border bg-card p-5 shadow-xs">
              <Skeleton className="h-5 w-40" />
              <Skeleton className="h-4 w-56" />
            </li>
          ))}
        </ul>
      </>
    );
  } else if (history.isError && items.length === 0) {
    body = (
      <Alert variant="destructive" role="alert">
        <AlertCircleIcon aria-hidden="true" />
        <AlertTitle>{loadErrorMessage(history.error, "The export history")}</AlertTitle>
        <AlertDescription>
          <Button
            type="button"
            variant="outline"
            className="mt-3 h-11 text-foreground"
            onClick={() => void history.refetch()}
          >
            Try again
          </Button>
        </AlertDescription>
      </Alert>
    );
  } else if (items.length === 0) {
    body = (
      <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed px-6 py-12 text-center">
        <span className="inline-flex size-12 items-center justify-center rounded-full bg-secondary text-muted-foreground">
          <FileSpreadsheetIcon className="size-6" aria-hidden="true" />
        </span>
        <h3 className="text-base font-semibold">No exports yet</h3>
      </div>
    );
  } else {
    body = (
      <div className="flex flex-col gap-4">
        <p className="text-sm text-muted-foreground">
          {items.length < total
            ? `Showing ${formatCount(items.length)} of ${formatCount(total)} exports`
            : `${formatCount(total)} ${total === 1 ? "export" : "exports"}`}
        </p>
        <ul aria-label="Export batches" className="flex flex-col gap-3">
          {items.map((batch) => (
            <HistoryRow key={batch.id} batch={batch} />
          ))}
        </ul>
        {history.isFetchNextPageError ? (
          <Alert variant="destructive" role="alert">
            <AlertCircleIcon aria-hidden="true" />
            <AlertDescription>
              More exports could not be loaded.{" "}
              {loadErrorMessage(history.error, "The export history")}
            </AlertDescription>
          </Alert>
        ) : null}
        {history.hasNextPage ? (
          <Button
            type="button"
            variant="outline"
            size="lg"
            className="self-center"
            disabled={history.isFetchingNextPage}
            onClick={() => void history.fetchNextPage()}
          >
            {history.isFetchingNextPage ? (
              <>
                <Spinner />
                Loading…
              </>
            ) : history.isFetchNextPageError ? (
              "Try loading more again"
            ) : (
              "Show more exports"
            )}
          </Button>
        ) : null}
      </div>
    );
  }

  return (
    <section aria-labelledby="export-history-heading" className="flex flex-col gap-4">
      <h2 id="export-history-heading" className="text-lg font-semibold">
        Export history
      </h2>
      {body}
    </section>
  );
}
