import type { Metadata } from "next";

import { QueueScreen } from "@/features/desk/components/queue-screen";

export const metadata: Metadata = {
  title: "Order queue",
  robots: { index: false },
};

export default function DeskPage() {
  return <QueueScreen />;
}
