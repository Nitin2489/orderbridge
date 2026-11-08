"use client";

import { AlertCircleIcon, InboxIcon, XIcon } from "lucide-react";
import Link from "next/link";
import { useMemo, useState, type ReactNode } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { FormMessage } from "@/components/ui/form-message";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { SignOutButton } from "@/features/auth/components/sign-out-button";
import { formatCount, formatIndiaDate, formatRupees } from "@/features/catalogue/format";
import { OrderStatusBadge } from "@/features/orders/components/order-status-badge";
import {
  FILTERABLE_STATUSES,
  hasConfirmedTotals,
  statusInfo,
  type OrderStatus,
} from "@/features/orders/status";
import { isApiError } from "@/lib/api";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { cn } from "@/lib/utils";

import { useDeskQueue, type QueueFilters } from "../api";
import { dateRange } from "../filters";
import { deskOrderPath } from "../paths";
import type { DeskOrderSummary } from "../types";

const SEARCH_MAX_LENGTH = 100;
const SEARCH_DEBOUNCE_MS = 300;

interface RetailerFilter {
  id: string;
  name: string;
}

function loadErrorMessage(error: unknown): string {
  if (isApiError(error) && error.status === 0) {
    return error.message;
  }
  if (isApiError(error) && error.status >= 500) {
    return "Something went wrong on our side. Please try again.";
  }
  return "The order queue could not be loaded.";
}

function Chip({
  label,
  pressed,
  onClick,
}: {
  label: string;
  pressed: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={cn(
        "inline-flex h-11 shrink-0 items-center rounded-full border px-4 text-sm font-medium transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
        pressed
          ? "border-primary bg-primary text-primary-foreground"
          : "bg-card text-foreground hover:bg-secondary",
      )}
    >
      {label}
    </button>
  );
}

function QueueRow({
  order,
  onRetailer,
}: {
  order: DeskOrderSummary;
  onRetailer: (retailer: RetailerFilter) => void;
}) {
  const placed = formatIndiaDate(order.created_at) ?? order.created_at;
  return (
    <li className="flex flex-col gap-3 rounded-xl border bg-card p-5 text-card-foreground shadow-xs sm:flex-row sm:items-center sm:justify-between">
      <div className="flex flex-col gap-1">
        <Link
          href={deskOrderPath(order.id)}
          className="w-fit rounded-sm font-mono font-semibold text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          {order.order_number}
        </Link>
        <button
          type="button"
          onClick={() => onRetailer({ id: order.retailer_id, name: order.retailer_business_name })}
          className="w-fit rounded-sm text-left text-sm font-medium underline-offset-4 outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50"
        >
          {order.retailer_business_name}
          <span className="sr-only">: show only this retailer&apos;s orders</span>
        </button>
        <span className="text-sm text-muted-foreground">
          Placed {placed} · {formatCount(order.item_count)}{" "}
          {order.item_count === 1 ? "product" : "products"}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-3 sm:justify-end">
        <OrderStatusBadge status={order.status} />
        <span className="text-sm">
          <span className="text-muted-foreground">
            {hasConfirmedTotals(order.status) ? "Confirmed total" : "Submitted total"}{" "}
          </span>
          <span className="font-semibold tabular-nums">{formatRupees(order.grand_total)}</span>
        </span>
      </div>
    </li>
  );
}

function QueueSkeleton() {
  return (
    <ul aria-hidden="true" className="flex flex-col gap-3">
      {Array.from({ length: 4 }, (_, index) => (
        <li key={index} className="flex flex-col gap-2 rounded-xl border bg-card p-5 shadow-xs">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-4 w-52" />
          <Skeleton className="h-4 w-64" />
        </li>
      ))}
    </ul>
  );
}

/** /desk: the order queue across retailers. Defaults to orders awaiting review. */
export function QueueScreen() {
  const [status, setStatus] = useState<OrderStatus | null>("pending_review");
  const [searchInput, setSearchInput] = useState("");
  const [retailer, setRetailer] = useState<RetailerFilter | null>(null);
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const search = useDebouncedValue(
    searchInput.trim().slice(0, SEARCH_MAX_LENGTH),
    SEARCH_DEBOUNCE_MS,
  );
  const range = dateRange(fromDate, toDate);

  const filters = useMemo<QueueFilters>(
    () => ({
      status,
      search,
      retailerId: retailer?.id ?? null,
      createdFrom: range.createdFrom,
      createdTo: range.createdTo,
    }),
    [status, search, retailer, range.createdFrom, range.createdTo],
  );
  const queue = useDeskQueue(filters);

  const pages = queue.data?.pages ?? [];
  const items = pages.flatMap((page) => page.items);
  const total = pages[0]?.total ?? 0;
  const updating =
    queue.isPlaceholderData || (queue.isFetching && !queue.isFetchingNextPage && !queue.isPending);
  const filtered =
    status !== null || search !== "" || retailer !== null || Boolean(fromDate || toDate);

  const clearFilters = () => {
    setStatus(null);
    setSearchInput("");
    setRetailer(null);
    setFromDate("");
    setToDate("");
  };

  let body: ReactNode;
  if (queue.isPending) {
    body = (
      <>
        <p role="status" className="sr-only">
          Loading orders…
        </p>
        <QueueSkeleton />
      </>
    );
  } else if (queue.isError && items.length === 0) {
    body = (
      <Alert variant="destructive" role="alert">
        <AlertCircleIcon aria-hidden="true" />
        <AlertTitle>{loadErrorMessage(queue.error)}</AlertTitle>
        <AlertDescription>
          <Button
            type="button"
            variant="outline"
            className="mt-3 h-11 text-foreground"
            onClick={() => void queue.refetch()}
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
          <InboxIcon className="size-6" aria-hidden="true" />
        </span>
        <h2 className="text-lg font-semibold">
          {status === "pending_review" && !search && !retailer && !fromDate && !toDate
            ? "No orders waiting for review"
            : filtered
              ? "No orders match these filters"
              : "No orders yet"}
        </h2>
        {filtered ? (
          <Button type="button" variant="outline" className="h-11" onClick={clearFilters}>
            Clear filters
          </Button>
        ) : null}
      </div>
    );
  } else {
    body = (
      <div className="flex flex-col gap-6">
        <ul
          aria-label="Orders"
          aria-busy={updating || undefined}
          className={cn("flex flex-col gap-3 transition-opacity", updating && "opacity-60")}
        >
          {items.map((order) => (
            <QueueRow key={order.id} order={order} onRetailer={setRetailer} />
          ))}
        </ul>
        {queue.isFetchNextPageError ? (
          <Alert variant="destructive" role="alert">
            <AlertCircleIcon aria-hidden="true" />
            <AlertDescription>
              More orders could not be loaded. {loadErrorMessage(queue.error)}
            </AlertDescription>
          </Alert>
        ) : null}
        {queue.hasNextPage ? (
          <Button
            type="button"
            variant="outline"
            size="lg"
            className="self-center"
            disabled={queue.isFetchingNextPage}
            onClick={() => void queue.fetchNextPage()}
          >
            {queue.isFetchingNextPage ? (
              <>
                <Spinner />
                Loading…
              </>
            ) : queue.isFetchNextPageError ? (
              "Try loading more again"
            ) : (
              "Show more orders"
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
          <p className="text-sm font-medium text-primary">Order desk</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight sm:text-3xl">Order queue</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Pending and held orders are listed oldest first. Amounts exclude GST; totals of
            unconfirmed orders are the prices at submission.
          </p>
        </div>
        <SignOutButton />
      </div>

      <div className="flex flex-col gap-4">
        <div
          role="group"
          aria-label="Filter by status"
          className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:flex-wrap sm:px-0"
        >
          <Chip label="All" pressed={status === null} onClick={() => setStatus(null)} />
          {FILTERABLE_STATUSES.map((value) => (
            <Chip
              key={value}
              label={statusInfo(value).label}
              pressed={status === value}
              onClick={() => setStatus(value)}
            />
          ))}
        </div>

        <div className="grid gap-4 sm:grid-cols-[1fr_auto_auto]">
          <div className="flex flex-col gap-2">
            <Label htmlFor="queue-search">Order number or retailer</Label>
            <Input
              id="queue-search"
              type="search"
              maxLength={SEARCH_MAX_LENGTH}
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="queue-from">Placed from</Label>
            <Input
              id="queue-from"
              type="date"
              value={fromDate}
              aria-invalid={range.error ? true : undefined}
              aria-describedby={range.error ? "queue-dates-error" : undefined}
              onChange={(event) => setFromDate(event.target.value)}
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="queue-to">Placed to</Label>
            <Input
              id="queue-to"
              type="date"
              value={toDate}
              aria-invalid={range.error ? true : undefined}
              aria-describedby={range.error ? "queue-dates-error" : undefined}
              onChange={(event) => setToDate(event.target.value)}
            />
          </div>
        </div>
        <FormMessage id="queue-dates-error">{range.error}</FormMessage>

        {retailer ? (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span>
              Retailer: <span className="font-medium">{retailer.name}</span>
            </span>
            <Button
              type="button"
              variant="ghost"
              className="h-11"
              onClick={() => setRetailer(null)}
            >
              <XIcon aria-hidden="true" />
              Show all retailers
            </Button>
          </div>
        ) : null}
      </div>

      <p
        role="status"
        aria-live="polite"
        className="flex min-h-5 items-center gap-2 text-sm text-muted-foreground"
      >
        {queue.isSuccess && !updating && items.length > 0
          ? items.length < total
            ? `Showing ${formatCount(items.length)} of ${formatCount(total)} orders`
            : `${formatCount(total)} ${total === 1 ? "order" : "orders"}`
          : null}
        {updating ? (
          <>
            <Spinner />
            Updating orders…
          </>
        ) : null}
      </p>

      {body}
    </div>
  );
}
