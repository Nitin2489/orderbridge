import type { Metadata } from "next";

import { OrderReviewScreen } from "@/features/desk/components/order-review-screen";

export const metadata: Metadata = {
  title: "Order review",
  robots: { index: false },
};

export default async function DeskOrderPage({
  params,
}: {
  params: Promise<{ orderId: string }>;
}) {
  const { orderId } = await params;
  // Keyed by id: another order starts with fresh action state.
  return <OrderReviewScreen key={orderId} orderId={orderId} />;
}
