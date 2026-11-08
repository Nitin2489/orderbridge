import type { Metadata } from "next";

import { CartScreen } from "@/features/cart/components/cart-screen";

export const metadata: Metadata = {
  title: "Cart",
  robots: { index: false },
};

export default function RetailerCartPage() {
  return <CartScreen />;
}
