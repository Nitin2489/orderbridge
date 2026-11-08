import type { NextConfig } from "next";

/**
 * Same-origin API proxy.
 *
 * The browser only ever calls relative /api/* paths on the frontend's own
 * origin. Next.js forwards them, server-side, to the FastAPI backend at
 * BACKEND_ORIGIN. The backend's session cookie is therefore first-party
 * (HttpOnly, SameSite=lax, host-only) and no CORS is needed in the browser.
 *
 * BACKEND_ORIGIN is server-only (never NEXT_PUBLIC_) and is read when the
 * rewrites are built, so it must be set at build time. Development falls
 * back to a local backend; production builds fail without it rather than
 * silently proxying to the wrong place.
 */
const DEVELOPMENT_BACKEND_ORIGIN = "http://127.0.0.1:8000";

function backendOrigin(): string {
  const configured = process.env.BACKEND_ORIGIN?.trim();

  if (!configured) {
    if (process.env.NODE_ENV === "production") {
      throw new Error("BACKEND_ORIGIN must be set for production builds.");
    }
    return DEVELOPMENT_BACKEND_ORIGIN;
  }

  const url = new URL(configured);
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  ) {
    throw new Error(
      "BACKEND_ORIGIN must be an origin such as https://api.example.com, " +
        "with no path, query or fragment.",
    );
  }
  return url.origin;
}

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendOrigin()}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
