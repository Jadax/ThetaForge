import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  basePath: process.env.GITHUB_PAGES === "true" ? "/ThetaForge" : "",
  assetPrefix: process.env.GITHUB_PAGES === "true" ? "/ThetaForge/" : undefined,
};

export default nextConfig;
