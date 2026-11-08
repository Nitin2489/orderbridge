import type { Metadata } from "next";

import { CatalogueScreen } from "@/features/catalogue/components/catalogue-screen";

export const metadata: Metadata = {
  title: "Catalogue",
  robots: { index: false },
};

export default function RetailerPage() {
  return <CatalogueScreen />;
}
