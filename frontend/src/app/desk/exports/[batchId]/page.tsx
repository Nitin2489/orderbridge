import type { Metadata } from "next";

import { BatchScreen } from "@/features/exports/components/batch-screen";

export const metadata: Metadata = {
  title: "Export batch",
  robots: { index: false },
};

export default async function DeskExportBatchPage({
  params,
}: {
  params: Promise<{ batchId: string }>;
}) {
  const { batchId } = await params;
  // Keyed by id: another batch starts with fresh download state.
  return <BatchScreen key={batchId} batchId={batchId} />;
}
