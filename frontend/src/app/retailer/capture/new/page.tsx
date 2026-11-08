import type { Metadata } from "next";

import { NewCaptureScreen } from "@/features/captures/components/new-capture-screen";

export const metadata: Metadata = {
  title: "Capture a message",
  robots: { index: false },
};

export default function NewCapturePage() {
  return <NewCaptureScreen role="retailer" />;
}
