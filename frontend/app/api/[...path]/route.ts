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
  // Тело читаем один раз: оно нужно и прокси, и mock-fallback.
  const rawBody = req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer()

  // 1) Пробуем реальный бэкенд
  try {
    const controller = new AbortController()
    // Обычные запросы — быстрый fallback на mock (1.5 с). Загрузка файлов
    // креативов (до 50 МБ видео) и экспорты не должны обрываться таймером.
    const isLong = /^\/creatives\/upload\b/.test(path) || /\/export\b/.test(path)
    const timer = setTimeout(() => controller.abort(), isLong ? 5 * 60_000 : 1500)
    const backendRes = await fetch(`${BACKEND}${path}${url.search}`, {
      method: req.method,
      headers: filterHeaders(req.headers),
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
  } catch {
    // бэкенда нет — переходим к mock
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
