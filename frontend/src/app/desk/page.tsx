import type { Metadata } from "next";

import { DeskHome } from "@/features/desk/components/desk-home";

export const metadata: Metadata = {
  title: "Order queue",
  robots: { index: false },
};

export default function DeskPage() {
  return <DeskHome />;
}
