import type { Metadata } from "next";

import { OrdersScreen } from "@/features/orders/components/orders-screen";

export const metadata: Metadata = {
  title: "Orders",
  robots: { index: false },
};

export default function RetailerOrdersPage() {
  return <OrdersScreen />;
}
