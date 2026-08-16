import type { NextConfig } from 'next'

const securityHeaders = [
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'Strict-Transport-Security', value: 'max-age=63072000' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
]

const nextConfig: NextConfig = {
  typescript: {
    ignoreBuildErrors: true,
  },
  async headers() {
    return [{ source: '/(.*)', headers: securityHeaders }]
  },
  async rewrites() {
    // In production Vercel routes /api/* to the Python service (vercel.json).
    // In the sandbox only `next dev` runs, so proxy /api/* to the local FastAPI.
    if (process.env.NODE_ENV === 'development') {
      return [{ source: '/api/:path*', destination: 'http://127.0.0.1:8000/:path*' }]
    }
    return []
  },
}

export default nextConfig
