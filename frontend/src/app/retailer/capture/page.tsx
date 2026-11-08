import type { Metadata } from "next";

import { CaptureListScreen } from "@/features/captures/components/capture-list-screen";

export const metadata: Metadata = {
  title: "Message capture",
  robots: { index: false },
};

export default function CapturesPage() {
  return <CaptureListScreen role="retailer" />;
}
