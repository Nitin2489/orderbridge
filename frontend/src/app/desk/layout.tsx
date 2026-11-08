import type { ReactNode } from "react";

import { RoleGate } from "@/features/auth/components/role-gate";

/** Everything under /desk is for the order_desk role only (UX guard; the API enforces it). */
export default function DeskLayout({ children }: { children: ReactNode }) {
  return <RoleGate role="order_desk">{children}</RoleGate>;
}
