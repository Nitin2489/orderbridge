import type { Metadata } from "next";

import { CaptureReviewScreen } from "@/features/captures/components/capture-review-screen";

export const metadata: Metadata = {
  title: "Review message",
  robots: { index: false },
};

export default async function CaptureReviewPage({
  params,
}: {
  params: Promise<{ captureId: string }>;
}) {
  const { captureId } = await params;
  // Keyed by id: another capture starts with fresh review state.
  return <CaptureReviewScreen key={captureId} captureId={captureId} role="order_desk" />;
}
