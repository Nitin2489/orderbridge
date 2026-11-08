"use client";

import { AlertCircleIcon, MessageSquareTextIcon, PlusIcon } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { formatCount, formatIndiaDateTime } from "@/features/catalogue/format";
import { isApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

import { useCaptures } from "../api";
import { capturePath, newCapturePath, type CaptureRole } from "../paths";
import type { CaptureStatus, CaptureSummary } from "../types";
import { CaptureStatusBadge, captureStatusLabel } from "./capture-status-badge";

const FILTERS: (CaptureStatus | null)[] = ["needs_review", "ready", "submitted", "discarded", null];

function loadErrorMessage(error: unknown): string {
  if (isApiError(error) && error.status === 0) {
    return error.message;
  }
  if (isApiError(error) && error.status >= 500) {
    return "Something went wrong on our side. Please try again.";
  }
  return "Captured messages could not be loaded.";
}

function CaptureRow({ capture, role }: { capture: CaptureSummary; role: CaptureRole }) {
  const received = formatIndiaDateTime(capture.created_at) ?? capture.created_at;
  return (
    <li>
      <Link
        href={capturePath(role, capture.id)}
        className="flex flex-col gap-2 rounded-xl border bg-card p-5 text-card-foreground shadow-xs transition-colors outline-none hover:bg-secondary/50 focus-visible:ring-[3px] focus-visible:ring-ring/50"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="font-medium">{capture.retailer.business_name}</span>
          <CaptureStatusBadge status={capture.status} />
        </div>
        <span className="line-clamp-2 text-sm">“{capture.message_preview}”</span>
        <span className="text-xs text-muted-foreground">
          {received} · {formatCount(capture.line_count)}{" "}
          {capture.line_count === 1 ? "line" : "lines"}
          {capture.unresolved_count > 0
            ? ` · ${formatCount(capture.unresolved_count)} to decide`
            : ""}
          {capture.order ? ` · Order ${capture.order.order_number}` : ""}
        </span>
      </Link>
    </li>
  );
}

/** Captured messages the signed-in user may see, newest first. */
export function CaptureListScreen({ role }: { role: CaptureRole }) {
  const [status, setStatus] = useState<CaptureStatus | null>("needs_review");
  const captures = useCaptures(status);
  const pages = captures.data?.pages ?? [];
  const items = pages.flatMap((page) => page.items);
  const total = pages[0]?.total ?? 0;

  let body: ReactNode;
  if (captures.isPending) {
    body = (
      <>
        <p role="status" className="sr-only">
          Loading captured messages…
        </p>
        <ul aria-hidden="true" className="flex flex-col gap-3">
          {Array.from({ length: 3 }, (_, index) => (
            <li key={index} className="flex flex-col gap-2 rounded-xl border bg-card p-5 shadow-xs">
              <Skeleton className="h-5 w-48" />
              <Skeleton className="h-4 w-full" />
            </li>
          ))}
        </ul>
      </>
    );
  } else if (captures.isError && items.length === 0) {
    body = (
      <Alert variant="destructive" role="alert">
        <AlertCircleIcon aria-hidden="true" />
        <AlertTitle>{loadErrorMessage(captures.error)}</AlertTitle>
        <AlertDescription>
          <Button
            type="button"
            variant="outline"
            className="mt-3 h-11 text-foreground"
            onClick={() => void captures.refetch()}
          >
            Try again
          </Button>
        </AlertDescription>
      </Alert>
    );
  } else if (items.length === 0) {
    body = (
      <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed px-6 py-16 text-center">
        <span className="inline-flex size-12 items-center justify-center rounded-full bg-secondary text-muted-foreground">
          <MessageSquareTextIcon className="size-6" aria-hidden="true" />
        </span>
        <h2 className="text-lg font-semibold">
          {status ? `No messages: ${captureStatusLabel(status).toLowerCase()}` : "No captured messages yet"}
        </h2>
        <Button asChild variant="outline" className="h-11">
          <Link href={newCapturePath(role)}>Capture a message</Link>
        </Button>
      </div>
    );
  } else {
    body = (
      <div className="flex flex-col gap-4">
        <p className="text-sm text-muted-foreground">
          {items.length < total
            ? `Showing ${formatCount(items.length)} of ${formatCount(total)} messages`
            : `${formatCount(total)} ${total === 1 ? "message" : "messages"}`}
        </p>
        <ul aria-label="Captured messages" className="flex flex-col gap-3">
          {items.map((capture) => (
            <CaptureRow key={capture.id} capture={capture} role={role} />
          ))}
        </ul>
        {captures.hasNextPage ? (
          <Button
            type="button"
            variant="outline"
            size="lg"
            className="self-center"
            disabled={captures.isFetchingNextPage}
            onClick={() => void captures.fetchNextPage()}
          >
            {captures.isFetchingNextPage ? (
              <>
                <Spinner />
                Loading…
              </>
            ) : (
              "Show more messages"
            )}
          </Button>
        ) : null}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-sm font-medium text-primary">
            {role === "order_desk" ? "Order desk" : "Your orders"}
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight sm:text-3xl">Message capture</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Paste an order message as it was sent. OrderBridge proposes each line with rule-based
            matching (not an AI model); a person decides every line before an order is created.
          </p>
        </div>
        <Button asChild size="lg" className="w-full sm:w-fit">
          <Link href={newCapturePath(role)}>
            <PlusIcon aria-hidden="true" />
            Capture a message
          </Link>
        </Button>
      </div>

      <div
        role="group"
        aria-label="Filter by status"
        className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:flex-wrap sm:px-0"
      >
        {FILTERS.map((value) => (
          <button
            key={value ?? "all"}
            type="button"
            aria-pressed={status === value}
            onClick={() => setStatus(value)}
            className={cn(
              "inline-flex h-11 shrink-0 items-center rounded-full border px-4 text-sm font-medium transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
              status === value
                ? "border-primary bg-primary text-primary-foreground"
                : "bg-card text-foreground hover:bg-secondary",
            )}
          >
            {value ? captureStatusLabel(value) : "All"}
          </button>
        ))}
      </div>

      {body}
    </div>
  );
}
