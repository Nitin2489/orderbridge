import { Badge } from "@/components/ui/badge";

import type { CaptureStatus } from "../types";

const STATUS: Record<
  CaptureStatus,
  { label: string; variant: "default" | "secondary" | "success" | "destructive" | "outline" }
> = {
  needs_review: { label: "Needs review", variant: "default" },
  ready: { label: "Ready to submit", variant: "secondary" },
  submitted: { label: "Order created", variant: "success" },
  discarded: { label: "Discarded", variant: "outline" },
  failed: { label: "Failed", variant: "destructive" },
};

export function captureStatusLabel(status: CaptureStatus): string {
  return STATUS[status].label;
}

export function CaptureStatusBadge({ status }: { status: CaptureStatus }) {
  const { label, variant } = STATUS[status];
  return <Badge variant={variant}>{label}</Badge>;
}
