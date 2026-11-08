import type { Metadata } from "next";

import { OrderDetailScreen } from "@/features/orders/components/order-detail-screen";

export const metadata: Metadata = {
  title: "Order",
  robots: { index: false },
};

export default async function RetailerOrderPage({
  params,
}: {
  params: Promise<{ orderId: string }>;
}) {
  const { orderId } = await params;
  // Keyed by id: moving to another order (e.g. a repeat's new order) starts fresh.
  return <OrderDetailScreen key={orderId} orderId={orderId} />;
}
