import type {NextConfig} from 'next';
const config: NextConfig = {
  // Convenience for frontend-only development. Compose routes /api directly through nginx.
  async rewrites() {return [{source:'/api/:path*', destination:`${process.env.BACKEND_URL || 'http://api:8000'}/api/:path*`}];},
};
export default config;
