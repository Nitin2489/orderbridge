import type { ReactNode } from "react";

import { RoleGate } from "@/features/auth/components/role-gate";

/** Everything under /retailer is for the retailer role only (UX guard; the API enforces it). */
export default function RetailerLayout({ children }: { children: ReactNode }) {
  return <RoleGate role="retailer">{children}</RoleGate>;
}
