import type { NextConfig } from 'next'

// CSP пока в режиме report-only: нарушения видны в консоли браузера, ничего не
// блокируется. После недели без репортов на проде переключить ключ на
// 'Content-Security-Policy' (см. docs/audit/prod_readiness_2026-09.md, R6).
//
// - script-src 'unsafe-inline': Next.js эмитит инлайновые hydration-скрипты;
//   следующий шаг — nonce через proxy.ts, тогда 'unsafe-inline' убрать.
// - img-src https: — изображения креативов лежат на VPS/CDN партнёров.
// - frame-src 'self' data: blob: — превью HTML5-баннера через <iframe srcDoc>
//   (about:srcdoc наследует политику страницы, но сам фрейм должен быть разрешён).
// - connect-src 'self' — весь API идёт через /api/* того же origin
//   (app/api/[...path]/route.ts или Python-функция в vercel.json).
const csp = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "frame-src 'self' data: blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'self'",
].join('; ')

const securityHeaders = [
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'Strict-Transport-Security', value: 'max-age=63072000' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
  // Кабинет — авторизованный state-changing UI; встраивание в чужие сайты не нужно.
  // Публичная страница /share/[token] тоже не предполагает embed.
  { key: 'X-Frame-Options', value: 'SAMEORIGIN' },
  { key: 'Content-Security-Policy-Report-Only', value: csp },
]

const nextConfig: NextConfig = {
  // ignoreBuildErrors снят (R6): tsc --noEmit чист, ошибки типов теперь валят сборку.
  async headers() {
    return [{ source: '/(.*)', headers: securityHeaders }]
  },
  // /api/* обрабатывает app/api/[...path]/route.ts: сначала проксирует на локальный
  // FastAPI (127.0.0.1:8000 или BACKEND_INTERNAL_URL), при его недоступности отвечает
  // demo-данными (lib/mock-api.ts) — так превью работает без Python/Postgres.
  // В проде vercel.json направляет /api/* на Python-функцию до Next.js.
}

export default nextConfig
