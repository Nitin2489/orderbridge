import { Badge } from "@/components/ui/badge";

import { statusInfo } from "../status";

export function OrderStatusBadge({ status }: { status: string }) {
  const info = statusInfo(status);
  return <Badge variant={info.variant}>{info.label}</Badge>;
}
