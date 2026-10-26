import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 课堂使用 127.0.0.1；允许本机来源加载开发脚本及连接热更新服务。
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  // 基础安全响应头：降低点击劫持、MIME 嗅探与 Referer 泄露风险。
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};

export default nextConfig;
