import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Lets CI or a validation build run without disturbing a live local dev server.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  basePath: process.env.GITHUB_PAGES === "true" ? "/ThetaForge" : "",
  assetPrefix: process.env.GITHUB_PAGES === "true" ? "/ThetaForge/" : undefined,
};

export default nextConfig;
