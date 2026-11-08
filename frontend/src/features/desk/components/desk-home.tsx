"use client";

import { useState } from "react";

import type { OrderStatus } from "@/features/orders/status";

import { DeskSummary } from "./desk-summary";
import { QueueScreen } from "./queue-screen";

/** /desk: what needs attention, then the order queue it filters. */
export function DeskHome() {
  const [status, setStatus] = useState<OrderStatus | null>("pending_review");
  return (
    <QueueScreen
      status={status}
      onStatusChange={setStatus}
      summary={<DeskSummary activeStatus={status} onSelectStatus={setStatus} />}
    />
  );
}
