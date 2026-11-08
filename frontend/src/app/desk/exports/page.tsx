import type { Metadata } from "next";

import { ExportScreen } from "@/features/exports/components/export-screen";

export const metadata: Metadata = {
  title: "Tally export",
  robots: { index: false },
};

export default function DeskExportsPage() {
  return <ExportScreen />;
}
