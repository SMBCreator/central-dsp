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
  // /api/* обрабатывает app/api/[...path]/route.ts: сначала проксирует на локальный
  // FastAPI (127.0.0.1:8000 или BACKEND_INTERNAL_URL), при его недоступности отвечает
  // demo-данными (lib/mock-api.ts) — так превью работает без Python/Postgres.
  // В проде vercel.json направляет /api/* на Python-функцию до Next.js.
}

export default nextConfig
