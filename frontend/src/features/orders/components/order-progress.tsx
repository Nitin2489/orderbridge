import {
  CircleCheckIcon,
  CircleDotIcon,
  CircleIcon,
  CirclePauseIcon,
  CircleXIcon,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";

import { orderProgress, type StepState } from "../progress";
import type { RetailerOrderSummary } from "../types";

const STATE: Record<StepState, { icon: LucideIcon; tone: string; spoken: string }> = {
  done: { icon: CircleCheckIcon, tone: "text-emerald-600 dark:text-emerald-400", spoken: "Done" },
  current: { icon: CircleDotIcon, tone: "text-primary", spoken: "In progress" },
  upcoming: { icon: CircleIcon, tone: "text-muted-foreground/60", spoken: "Not yet" },
  held: { icon: CirclePauseIcon, tone: "text-amber-600 dark:text-amber-400", spoken: "On hold" },
  stopped: { icon: CircleXIcon, tone: "text-destructive", spoken: "Stopped" },
};

/** Where the order is inside OrderBridge. Nothing about delivery. */
export function OrderProgress({
  order,
  rejectionReason = null,
}: {
  order: Pick<RetailerOrderSummary, "status" | "created_at" | "confirmed_at">;
  /** The order desk's reason, shown only for a rejected order. */
  rejectionReason?: string | null;
}) {
  const steps = orderProgress(order);
  if (!steps) {
    return null;
  }

  return (
    <section aria-labelledby="order-progress-heading" className="rounded-xl border bg-card p-5 shadow-xs">
      <h2 id="order-progress-heading" className="text-base font-semibold">
        Progress
      </h2>
      <ol className="mt-4 flex flex-col gap-4 sm:flex-row sm:gap-2">
        {steps.map((entry, index) => {
          const { icon: Icon, tone, spoken } = STATE[entry.state];
          const last = index === steps.length - 1;
          return (
            <li
              key={entry.key}
              aria-current={entry.state === "current" || entry.state === "held" ? "step" : undefined}
              className="flex items-start gap-3 sm:flex-1 sm:flex-col sm:gap-2"
            >
              <div className="flex items-center gap-2 sm:w-full">
                <Icon aria-hidden="true" className={cn("size-5 shrink-0", tone)} />
                {!last ? (
                  <span aria-hidden="true" className="hidden h-px flex-1 bg-border sm:block" />
                ) : null}
              </div>
              <div className="text-sm">
                <p className={cn("font-medium", entry.state === "upcoming" && "text-muted-foreground")}>
                  <span className="sr-only">{spoken}: </span>
                  {entry.label}
                </p>
                {entry.date ? <p className="text-xs text-muted-foreground">{entry.date}</p> : null}
              </div>
            </li>
          );
        })}
      </ol>
      {order.status === "rejected" && rejectionReason ? (
        <div className="mt-4 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm">
          <p className="font-medium">Reason from the order desk</p>
          <p className="mt-1 break-words whitespace-pre-line">{rejectionReason}</p>
        </div>
      ) : null}
    </section>
  );
}
