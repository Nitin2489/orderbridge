import type { badgeVariants } from "@/components/ui/badge";
import type { VariantProps } from "class-variance-authority";

/**
 * Order statuses (backend OrderStatus) as retailers see them. Reasons for
 * holds and rejections are not exposed to retailers, so none are invented.
 */
export const ORDER_STATUSES = [
  "pending_review",
  "on_hold",
  "confirmed",
  "rejected",
  "exported",
  "draft",
] as const;

export type OrderStatus = (typeof ORDER_STATUSES)[number];

/** Filter options: draft is left out because the retailer app never creates drafts. */
export const FILTERABLE_STATUSES: readonly OrderStatus[] = ORDER_STATUSES.filter(
  (status) => status !== "draft",
);

type BadgeVariant = NonNullable<VariantProps<typeof badgeVariants>["variant"]>;

interface StatusInfo {
  label: string;
  description: string;
  variant: BadgeVariant;
}

const STATUS_INFO: Record<OrderStatus, StatusInfo> = {
  pending_review: {
    label: "Pending review",
    description: "Waiting for order-desk review.",
    variant: "default",
  },
  on_hold: {
    label: "On hold",
    description: "Order is on hold and needs order-desk attention.",
    variant: "secondary",
  },
  confirmed: { label: "Confirmed", description: "Order confirmed.", variant: "success" },
  rejected: { label: "Rejected", description: "Order rejected.", variant: "destructive" },
  exported: {
    label: "Exported",
    description: "Order exported for billing.",
    variant: "success",
  },
  draft: { label: "Draft", description: "Draft order.", variant: "outline" },
};

export function isOrderStatus(value: string): value is OrderStatus {
  return (ORDER_STATUSES as readonly string[]).includes(value);
}

/** Label, description and badge style; an unknown status is shown as-is. */
export function statusInfo(status: string): StatusInfo {
  if (isOrderStatus(status)) {
    return STATUS_INFO[status];
  }
  const label = status.replace(/_/g, " ");
  return { label, description: "", variant: "outline" };
}

/** Totals are final once the order desk has confirmed (and possibly re-priced) the order. */
export function hasConfirmedTotals(status: string): boolean {
  return status === "confirmed" || status === "exported";
}

const SOURCE_LABEL: Record<string, string> = {
  retailer_app: "Retailer app",
  ai_text: "Message capture",
  salesman: "Salesman",
};

export function sourceLabel(source: string): string {
  return SOURCE_LABEL[source] ?? source.replace(/_/g, " ");
}
