/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // 后端默认跑在 8001,避免和本机已有服务抢 8000。
  async rewrites() {
    const apiPort = process.env.NEXT_PUBLIC_API_PORT || "8001";
    return [
      { source: "/api/:path*", destination: `http://localhost:${apiPort}/api/:path*` },
    ];
  },
};

module.exports = nextConfig;
