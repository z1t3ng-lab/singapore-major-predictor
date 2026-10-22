import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The desktop app has a separate production build from the browser dev server.
  distDir: process.env.MAJOR_DESKTOP === "1" ? ".next-desktop" : ".next",
  reactStrictMode: true,
  poweredByHeader: false,
  allowedDevOrigins: ["127.0.0.1"],
};

export default nextConfig;
