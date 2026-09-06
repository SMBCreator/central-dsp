import { resolveMock } from "@/lib/mock-api"

/**
 * Catch-all для /api/* внутри Next.js.
 *
 * Приоритет — реальный FastAPI-бэкенд (proxy). Demo-данные из lib/mock-api
 * отдаются ТОЛЬКО когда фолбэк включён явно (см. MOCK_FALLBACK): молчаливая
 * подмена реальных цифр на фейковые на стейдже/VPS недопустима.
 *
 * В проде этот код не участвует в обработке /api: vercel.json маршрутизирует
 * /api/* на Python-функцию до того, как запрос дойдёт до Next.js.
 */

const BACKEND = process.env.BACKEND_INTERNAL_URL || "http://127.0.0.1:8000"

/**
 * Mock-fallback: ALLOW_MOCK_FALLBACK=1 включает, =0 выключает. Без переменной —
 * включён только в dev-сборке (превью v0 без Python/Postgres), в production
 * всегда выключен: бэкенд недоступен → честный 503, а не демо-данные.
 */
const MOCK_FALLBACK =
  process.env.ALLOW_MOCK_FALLBACK === "1" ||
  (process.env.ALLOW_MOCK_FALLBACK !== "0" && process.env.NODE_ENV !== "production")

// Без мока нет смысла обрывать медленный бэкенд через 1.5 с — ждём как обычный прокси.
const FAST_FALLBACK_MS = 1500
const PROXY_TIMEOUT_MS = 30_000
const LONG_TIMEOUT_MS = 5 * 60_000

async function proxyOrMock(req: Request, params: Promise<{ path: string[] }>) {
  const { path: segments } = await params
  const url = new URL(req.url)
  const path = `/${segments.join("/")}`
  // Тело читаем один раз: оно нужно и прокси, и mock-fallback.
  const rawBody = req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer()

  // 1) Пробуем реальный бэкенд
  let backendError: unknown
  try {
    const controller = new AbortController()
    // Загрузка файлов креативов (до 50 МБ видео) и экспорты не должны обрываться таймером.
    const isLong = /^\/creatives\/upload\b/.test(path) || /\/export\b/.test(path)
    const timeout = isLong ? LONG_TIMEOUT_MS : MOCK_FALLBACK ? FAST_FALLBACK_MS : PROXY_TIMEOUT_MS
    const timer = setTimeout(() => controller.abort(), timeout)
    const backendRes = await fetch(`${BACKEND}${path}${url.search}`, {
      method: req.method,
      headers: filterHeaders(req.headers, url),
      body: rawBody,
      signal: controller.signal,
      // @ts-expect-error duplex обязателен в Node для стриминга тела
      duplex: "half",
    })
    clearTimeout(timer)
    return new Response(backendRes.body, {
      status: backendRes.status,
      headers: backendRes.headers,
    })
  } catch (e) {
    backendError = e
  }

  if (!MOCK_FALLBACK) {
    const reason = backendError instanceof Error ? backendError.name : "unknown"
    return Response.json(
      { detail: "Сервис временно недоступен: нет связи с бэкендом. Повторите через минуту." },
      { status: 503, headers: { "Retry-After": "30", "x-dsp-backend-error": reason } },
    )
  }

  // 2) Mock-fallback (демо-режим превью)
  let parsedBody: unknown
  if (rawBody && rawBody.byteLength) {
    try {
      parsedBody = JSON.parse(new TextDecoder().decode(rawBody))
    } catch {
      // multipart/бинарь — мок без тела
    }
  }
  const mock = resolveMock(req.method, path, url.searchParams, parsedBody)
  if (mock !== undefined) {
    const csv = mock as { __csv?: string; __filename?: string }
    if (csv && typeof csv.__csv === "string") {
      return new Response(csv.__csv, {
        headers: {
          "x-dsp-mock": "1",
          "Content-Type": "text/csv; charset=utf-8",
          "Content-Disposition": `attachment; filename="${csv.__filename || "export.csv"}"`,
        },
      })
    }
    return Response.json(mock, { headers: { "x-dsp-mock": "1" } })
  }
  // Неизвестные мутации в демо-режиме "принимаем", чтобы UI не падал
  if (req.method !== "GET") {
    return Response.json({ ok: true, demo: true }, { status: 200, headers: { "x-dsp-mock": "1" } })
  }
  return Response.json([], { headers: { "x-dsp-mock": "1" } })
}

function filterHeaders(headers: Headers, url: URL): Headers {
  const out = new Headers()
  for (const [k, v] of headers.entries()) {
    // host/connection и пр. не пробрасываем
    if (["host", "connection", "content-length"].includes(k.toLowerCase())) continue
    out.set(k, v)
  }
  // Origin/Referer уходят как есть; Origin-гейт бэкенда (S-01) сверяет их с
  // origin запроса, который за прокси виден только через X-Forwarded-*.
  if (!out.has("x-forwarded-host")) out.set("x-forwarded-host", url.host)
  if (!out.has("x-forwarded-proto")) out.set("x-forwarded-proto", url.protocol.replace(":", ""))
  return out
}

type Ctx = { params: Promise<{ path: string[] }> }

export async function GET(req: Request, ctx: Ctx) {
  return proxyOrMock(req, ctx.params)
}
export async function POST(req: Request, ctx: Ctx) {
  return proxyOrMock(req, ctx.params)
}
export async function PUT(req: Request, ctx: Ctx) {
  return proxyOrMock(req, ctx.params)
}
export async function PATCH(req: Request, ctx: Ctx) {
  return proxyOrMock(req, ctx.params)
}
export async function DELETE(req: Request, ctx: Ctx) {
  return proxyOrMock(req, ctx.params)
}
