"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import { formatCount } from "@/features/catalogue/format";
import { fetchCapturePage } from "@/features/captures/api";
import { CAPTURE_BASE } from "@/features/captures/paths";
import { useEligibleExport } from "@/features/exports/api";
import { EXPORTS_PATH } from "@/features/exports/paths";
import type { OrderStatus } from "@/features/orders/status";
import { cn } from "@/lib/utils";

import { deskKeys, deskQueueOptions, type QueueFilters } from "../api";

const NO_FILTERS: Omit<QueueFilters, "status"> = {
  search: "",
  retailerId: null,
  createdFrom: null,
  createdTo: null,
};

function useOrderCount(status: OrderStatus) {
  const queryClient = useQueryClient();
  return useQuery({
    // Its own key, under deskKeys.all, so it refreshes whenever the desk's
    // orders change (unchanged invalidation).
    queryKey: [...deskKeys.all, "count", status],
    // Fetched through the queue's own query (same key and first page): when
    // the queue shows this status unfiltered, as the overview does for
    // pending orders, the two share one request instead of sending two.
    queryFn: async () =>
      (await queryClient.fetchInfiniteQuery(deskQueueOptions({ ...NO_FILTERS, status }))).pages[0]!
        .total,
  });
}

function useCaptureCount() {
  return useQuery({
    // Under the captures list key, so it refreshes with every capture change.
    queryKey: ["captures", "list", "count", "needs_review"],
    queryFn: async ({ signal }) => (await fetchCapturePage("needs_review", 0, signal)).total,
  });
}

const TILE =
  "flex min-h-24 flex-col justify-between gap-2 rounded-xl border bg-card p-4 text-left shadow-xs transition-colors outline-none hover:bg-secondary/50 focus-visible:ring-[3px] focus-visible:ring-ring/50";

function Count({ value, pending }: { value: number | undefined; pending: boolean }) {
  if (pending) {
    return <Skeleton className="h-8 w-10" />;
  }
  return (
    <span className="text-3xl font-semibold tracking-tight tabular-nums">
      {value === undefined ? "–" : formatCount(value)}
    </span>
  );
}

function TileText({ label, hint }: { label: string; hint: string }): ReactNode {
  return (
    <span className="flex flex-col">
      <span className="text-sm font-medium">{label}</span>
      <span className="text-xs text-muted-foreground">{hint}</span>
    </span>
  );
}

/** What needs the order desk's attention now, each a way into that work. */
export function DeskSummary({
  activeStatus,
  onSelectStatus,
}: {
  activeStatus: OrderStatus | null;
  onSelectStatus: (status: OrderStatus) => void;
}) {
  const pending = useOrderCount("pending_review");
  const held = useOrderCount("on_hold");
  const captures = useCaptureCount();
  const eligible = useEligibleExport();

  return (
    <section aria-labelledby="desk-summary-heading" className="flex flex-col gap-3">
      <h2 id="desk-summary-heading" className="sr-only">
        At a glance
      </h2>
      <ul className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <li>
          <button
            type="button"
            aria-pressed={activeStatus === "pending_review"}
            onClick={() => onSelectStatus("pending_review")}
            className={cn(TILE, "w-full", activeStatus === "pending_review" && "border-primary")}
          >
            <Count value={pending.data} pending={pending.isPending} />
            <TileText label="To review" hint="Orders pending review" />
          </button>
        </li>
        <li>
          <button
            type="button"
            aria-pressed={activeStatus === "on_hold"}
            onClick={() => onSelectStatus("on_hold")}
            className={cn(TILE, "w-full", activeStatus === "on_hold" && "border-primary")}
          >
            <Count value={held.data} pending={held.isPending} />
            <TileText label="On credit hold" hint="Held at confirmation" />
          </button>
        </li>
        <li>
          <Link href={CAPTURE_BASE.order_desk} className={TILE}>
            <Count value={captures.data} pending={captures.isPending} />
            <TileText label="Messages to review" hint="Captured, not yet decided" />
          </Link>
        </li>
        <li>
          <Link href={EXPORTS_PATH} className={TILE}>
            <Count value={eligible.data?.order_count} pending={eligible.isPending} />
            <TileText label="Waiting for Tally" hint="Confirmed, not exported" />
          </Link>
        </li>
      </ul>
    </section>
  );
}
