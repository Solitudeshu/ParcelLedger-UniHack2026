import type { NextConfig } from "next";
import path from "node:path";

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Build relocatable standalone output in Linux; Windows builds remain unchanged.
  ...(process.env.KC_STANDALONE === "1" ? {
    output: "standalone" as const,
    outputFileTracingRoot: path.join(__dirname, "../../"),
  } : {}),
  async rewrites() {
    return [{source: "/api/:path*", destination: `${process.env.API_UPSTREAM || "http://127.0.0.1:8000"}/:path*`}];
  },
  async headers() {
    return [{source: "/:path*", headers: [
      {key: "X-Content-Type-Options", value: "nosniff"},
      {key: "Referrer-Policy", value: "no-referrer"},
      {key: "Permissions-Policy", value: "camera=(self), microphone=(), geolocation=()"},
      {key: "X-Frame-Options", value: "DENY"},
    ]}];
  },
};
export default config;
