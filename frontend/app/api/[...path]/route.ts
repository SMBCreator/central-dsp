import { resolveMock } from "@/lib/mock-api"

/**
 * Catch-all для /api/* внутри Next.js.
 *
 * Приоритет — реальный FastAPI-бэкенд (proxy). Если он недоступен (например,
 * превью v0 без Python/Postgres) — отвечаем demo-данными из lib/mock-api.
 *
 * В проде этот код не участвует в обработке /api: vercel.json маршрутизирует
 * /api/* на Python-функцию до того, как запрос дойдёт до Next.js.
 */

const BACKEND = process.env.BACKEND_INTERNAL_URL || "http://127.0.0.1:8000"

async function proxyOrMock(req: Request, params: Promise<{ path: string[] }>) {
  const { path: segments } = await params
  const url = new URL(req.url)
  const path = `/${segments.join("/")}`

  // 1) Пробуем реальный бэкенд
  try {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 1500)
    const backendRes = await fetch(`${BACKEND}${path}${url.search}`, {
      method: req.method,
      headers: filterHeaders(req.headers),
      body: req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer(),
      signal: controller.signal,
      // @ts-expect-error duplex обязателен в Node для стриминга тела
      duplex: "half",
    })
    clearTimeout(timer)
    return new Response(backendRes.body, {
      status: backendRes.status,
      headers: backendRes.headers,
    })
  } catch {
    // бэкенда нет — переходим к mock
  }

  // 2) Mock-fallback (демо-режим превью)
  const mock = resolveMock(req.method, path, url.searchParams)
  if (mock !== undefined) {
    return Response.json(mock, { headers: { "x-dsp-mock": "1" } })
  }
  // Неизвестные мутации в демо-режиме "принимаем", чтобы UI не падал
  if (req.method !== "GET") {
    return Response.json({ ok: true, demo: true }, { status: 200, headers: { "x-dsp-mock": "1" } })
  }
  return Response.json([], { headers: { "x-dsp-mock": "1" } })
}

function filterHeaders(headers: Headers): Headers {
  const out = new Headers()
  for (const [k, v] of headers.entries()) {
    // host/connection и пр. не пробрасываем
    if (["host", "connection", "content-length"].includes(k.toLowerCase())) continue
    out.set(k, v)
  }
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
