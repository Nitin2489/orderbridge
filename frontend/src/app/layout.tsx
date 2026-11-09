import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import type { ReactNode } from "react";

import { AppShell } from "@/components/layout/app-shell";
import { AppProviders } from "@/providers/app-providers";
import { ThemeScript } from "@/providers/theme-provider";

import "./globals.css";

// Inter, self-hosted: the Latin subset, plus a ~1 KB file holding only the ₹
// sign, so price pages do not download Inter's 85 KB Latin Extended subset
// for one glyph (see src/app/fonts/README.md). No external font requests.
const inter = localFont({
  src: "./fonts/inter-latin-var.woff2",
  weight: "100 900",
  style: "normal",
  display: "swap",
  variable: "--font-inter",
});

const interRupee = localFont({
  src: "./fonts/inter-rupee-var.woff2",
  weight: "100 900",
  style: "normal",
  display: "swap",
  variable: "--font-inter-rupee",
  preload: false,
  adjustFontFallback: false,
  declarations: [{ prop: "unicode-range", value: "U+20B9" }],
});

export const metadata: Metadata = {
  title: {
    default: "OrderBridge",
    template: "%s · OrderBridge",
  },
  description:
    "Retailer ordering and order operations for FMCG distributors: validated, reviewable and accurately priced orders.",
  applicationName: "OrderBridge",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f8fafc" },
    { media: "(prefers-color-scheme: dark)", color: "#0b132b" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${interRupee.variable}`}
      suppressHydrationWarning
    >
      <head>
        <ThemeScript />
      </head>
      <body className="font-sans">
        <AppProviders>
          <AppShell>{children}</AppShell>
        </AppProviders>
      </body>
    </html>
  );
}
