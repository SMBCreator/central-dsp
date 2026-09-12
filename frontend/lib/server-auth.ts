import { createHash } from "node:crypto"

/**
 * Серверная проверка сессии для Next route handlers (audit S-05).
 *
 * Единственный источник истины о сессии — backend `/auth/me`. Здесь нет своей
 * логики авторизации: мы лишь пробрасываем cookie `cdsp_session` и кэшируем
 * положительный ответ на 30 с по SHA-256 токена (сам токен в памяти не храним).
 * Отрицательные ответы не кэшируются: отозванная сессия должна закрываться сразу.
 */

export const SESSION_COOKIE = "cdsp_session"
const CACHE_TTL_MS = 30_000
const AUTH_TIMEOUT_MS = 5_000

function backendUrl(): string {
  return process.env.BACKEND_INTERNAL_URL || "http://127.0.0.1:8000"
}

export interface SessionUser {
  id: string
  role: string
  permissions: Record<string, boolean>
}

export class HttpError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    public readonly headers: Record<string, string> = {},
  ) {
    super(detail)
  }

  toResponse(): Response {
    return Response.json({ detail: this.detail }, { status: this.status, headers: this.headers })
  }
}

interface CacheEntry {
  user: SessionUser
  expiresAt: number
}

const cache = new Map<string, CacheEntry>()

export function readSessionToken(req: Request): string | null {
  const header = req.headers.get("cookie")
  if (!header) return null
  for (const part of header.split(";")) {
    const eq = part.indexOf("=")
    if (eq === -1) continue
    const name = part.slice(0, eq).trim()
    if (name !== SESSION_COOKIE) continue
    const value = part.slice(eq + 1).trim()
    return value.length ? value : null
  }
  return null
}

function tokenKey(token: string): string {
  return createHash("sha256").update(token).digest("hex")
}

function evictExpired(now: number) {
  if (cache.size < 1000) return
  for (const [key, entry] of cache) {
    if (entry.expiresAt <= now) cache.delete(key)
  }
}

export async function requireUser(req: Request): Promise<SessionUser> {
  const token = readSessionToken(req)
  if (!token) throw new HttpError(401, "Требуется вход в систему")

  const key = tokenKey(token)
  const now = Date.now()
  const cached = cache.get(key)
  if (cached && cached.expiresAt > now) return cached.user

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), AUTH_TIMEOUT_MS)
  let res: Response
  try {
    res = await fetch(`${backendUrl()}/auth/me`, {
      method: "GET",
      headers: { cookie: `${SESSION_COOKIE}=${token}`, accept: "application/json" },
      signal: controller.signal,
      cache: "no-store",
    })
  } catch {
    throw new HttpError(503, "Сервис авторизации недоступен. Повторите через минуту.", { "Retry-After": "30" })
  } finally {
    clearTimeout(timer)
  }

  if (res.status === 401 || res.status === 403) {
    cache.delete(key)
    throw new HttpError(401, "Сессия недействительна. Войдите заново.")
  }
  if (!res.ok) {
    throw new HttpError(503, "Сервис авторизации недоступен. Повторите через минуту.", { "Retry-After": "30" })
  }

  const body = (await res.json()) as { id?: unknown; role?: unknown; permissions?: unknown }
  if (typeof body.id !== "string" || typeof body.role !== "string") {
    throw new HttpError(503, "Некорректный ответ сервиса авторизации")
  }
  const user: SessionUser = {
    id: body.id,
    role: body.role,
    permissions:
      body.permissions && typeof body.permissions === "object"
        ? (body.permissions as Record<string, boolean>)
        : {},
  }
  evictExpired(now)
  cache.set(key, { user, expiresAt: now + CACHE_TTL_MS })
  return user
}

/** Сброс кэша между тестами. В production не вызывается. */
export function _resetSessionCacheForTests() {
  cache.clear()
}
