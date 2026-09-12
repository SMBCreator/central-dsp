import type { ZodType } from "zod"
import { HttpError, requireUser, type SessionUser } from "@/lib/server-auth"

/**
 * Общая защита платных AI route handlers (audit S-05):
 *   1) сессия через backend `/auth/me` (401);
 *   2) AI Gateway настроен (иначе 503, а не 500 со стеком);
 *   3) лимит размера тела ДО JSON.parse (413);
 *   4) валидация схемы zod (422);
 *   5) rate limit per user: token bucket в памяти процесса + Redis INCR/EXPIRE,
 *      если задан REDIS_URL (429 + Retry-After);
 *   6) структурированный JSON-лог в stdout: user_id, route, status, latency_ms, токены.
 */

export const MAX_BODY_BYTES = 32 * 1024
export const RATE_LIMIT_PER_MINUTE = 20

// --- AI Gateway configured? -------------------------------------------------

/**
 * На Vercel аутентификация в AI Gateway идёт через OIDC без ключа; в Compose/VPS
 * топологии ключ обязателен. Без того и другого честно отвечаем 503.
 */
export function aiConfigured(env: NodeJS.ProcessEnv = process.env): boolean {
  if (env.AI_GATEWAY_API_KEY) return true
  if (env.VERCEL_OIDC_TOKEN) return true
  if (env.VERCEL === "1") return true
  return false
}

// --- Body limit + schema -----------------------------------------------------

export async function readJsonBody<T>(req: Request, schema: ZodType<T>): Promise<T> {
  const declared = Number(req.headers.get("content-length") ?? "0")
  if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) {
    throw new HttpError(413, `Слишком большой запрос (лимит ${MAX_BODY_BYTES / 1024} КБ)`)
  }
  const text = await req.text()
  if (Buffer.byteLength(text, "utf8") > MAX_BODY_BYTES) {
    throw new HttpError(413, `Слишком большой запрос (лимит ${MAX_BODY_BYTES / 1024} КБ)`)
  }
  let raw: unknown
  try {
    raw = JSON.parse(text)
  } catch {
    throw new HttpError(400, "Некорректный JSON")
  }
  const parsed = schema.safeParse(raw)
  if (!parsed.success) {
    const first = parsed.error.issues[0]
    const where = first?.path?.length ? `${first.path.join(".")}: ` : ""
    throw new HttpError(422, `Некорректные данные запроса. ${where}${first?.message ?? ""}`.trim())
  }
  return parsed.data
}

// --- Rate limit --------------------------------------------------------------

interface Bucket {
  tokens: number
  updatedAt: number
}

const buckets = new Map<string, Bucket>()

/** Token bucket: ёмкость = RATE_LIMIT_PER_MINUTE, пополнение равномерное за минуту. */
export function consumeMemoryToken(userId: string, now = Date.now()): { ok: boolean; retryAfterSec: number } {
  const capacity = RATE_LIMIT_PER_MINUTE
  const refillPerMs = capacity / 60_000
  let bucket = buckets.get(userId)
  if (!bucket) {
    bucket = { tokens: capacity, updatedAt: now }
    buckets.set(userId, bucket)
  } else {
    const elapsed = Math.max(0, now - bucket.updatedAt)
    bucket.tokens = Math.min(capacity, bucket.tokens + elapsed * refillPerMs)
    bucket.updatedAt = now
  }
  if (bucket.tokens >= 1) {
    bucket.tokens -= 1
    return { ok: true, retryAfterSec: 0 }
  }
  const deficitMs = (1 - bucket.tokens) / refillPerMs
  return { ok: false, retryAfterSec: Math.max(1, Math.ceil(deficitMs / 1000)) }
}

type RedisLike = {
  incr(key: string): Promise<number>
  expire(key: string, seconds: number): Promise<unknown>
}

let redisClient: RedisLike | null | undefined

async function getRedis(): Promise<RedisLike | null> {
  if (redisClient !== undefined) return redisClient
  const url = process.env.AI_RATE_LIMIT_REDIS_URL || process.env.REDIS_URL
  if (!url) {
    redisClient = null
    return null
  }
  try {
    const mod = await import("ioredis")
    const Redis = mod.default
    const client = new Redis(url, {
      lazyConnect: true,
      maxRetriesPerRequest: 1,
      connectTimeout: 2_000,
      enableOfflineQueue: false,
    })
    client.on("error", (err: Error) => {
      logJson({ level: "warn", event: "ai_rate_limit_redis_error", err_type: err.name, err_msg: err.message })
    })
    await client.connect()
    redisClient = client
  } catch (err) {
    logJson({
      level: "warn",
      event: "ai_rate_limit_redis_unavailable",
      err_msg: err instanceof Error ? err.message : String(err),
    })
    redisClient = null
  }
  return redisClient
}

/**
 * Проверка лимита. Локальный bucket — всегда (защищает даже без Redis);
 * Redis — общий счётчик по минуте для нескольких инстансов. Ошибка Redis
 * не блокирует запрос: локальный лимит уже сработал как нижняя граница.
 */
export async function enforceRateLimit(userId: string): Promise<void> {
  const local = consumeMemoryToken(userId)
  if (!local.ok) {
    throw new HttpError(429, "Слишком много запросов. Подождите минуту.", {
      "Retry-After": String(local.retryAfterSec),
    })
  }
  const redis = await getRedis()
  if (!redis) return
  const minute = Math.floor(Date.now() / 60_000)
  const key = `ai:rl:${userId}:${minute}`
  try {
    const count = await redis.incr(key)
    if (count === 1) await redis.expire(key, 120)
    if (count > RATE_LIMIT_PER_MINUTE) {
      const retryAfter = 60 - (Math.floor(Date.now() / 1000) % 60)
      throw new HttpError(429, "Слишком много запросов. Подождите минуту.", {
        "Retry-After": String(Math.max(1, retryAfter)),
      })
    }
  } catch (err) {
    if (err instanceof HttpError) throw err
    // Redis недоступен — локальный лимит остаётся в силе.
  }
}

// --- Logging -----------------------------------------------------------------

export function logJson(fields: Record<string, unknown>): void {
  const line = JSON.stringify({ ts: new Date().toISOString(), service: "frontend", ...fields })
  if (fields.level === "error") console.error(line)
  else console.log(line)
}

// --- Orchestration -----------------------------------------------------------

export interface GuardedContext<T> {
  user: SessionUser
  body: T
  /** Завершает запрос: пишет лог и возвращает Response. */
  finish(status: number, payload: unknown, usage?: { inputTokens?: number; outputTokens?: number }): Response
  /** Ошибка вызова gateway: лог + 502 без внутренних деталей. */
  fail(err: unknown): Response
}

export async function guardAiRequest<T>(
  req: Request,
  route: string,
  schema: ZodType<T>,
  handler: (ctx: GuardedContext<T>) => Promise<Response>,
): Promise<Response> {
  const startedAt = Date.now()
  let userId: string | undefined
  const log = (status: number, extra: Record<string, unknown> = {}) =>
    logJson({
      level: status >= 500 ? "error" : status >= 400 ? "warn" : "info",
      event: "ai_request",
      route,
      user_id: userId ?? null,
      status,
      latency_ms: Date.now() - startedAt,
      ...extra,
    })

  try {
    const user = await requireUser(req)
    userId = user.id
    if (!aiConfigured()) {
      throw new HttpError(503, "AI недоступен: не настроен ключ AI Gateway", { "Retry-After": "300" })
    }
    await enforceRateLimit(user.id)
    const body = await readJsonBody(req, schema)

    const ctx: GuardedContext<T> = {
      user,
      body,
      finish(status, payload, usage) {
        log(status, { tokens_in: usage?.inputTokens ?? null, tokens_out: usage?.outputTokens ?? null })
        return Response.json(payload, { status, headers: { "Cache-Control": "no-store" } })
      },
      fail(err) {
        log(502, { err_type: err instanceof Error ? err.name : typeof err, err_msg: safeErrMsg(err) })
        return Response.json(
          { detail: "AI-сервис временно недоступен. Попробуйте ещё раз." },
          { status: 502, headers: { "Cache-Control": "no-store", "Retry-After": "30" } },
        )
      },
    }
    return await handler(ctx)
  } catch (err) {
    if (err instanceof HttpError) {
      log(err.status)
      const res = err.toResponse()
      res.headers.set("Cache-Control", "no-store")
      return res
    }
    log(500, { err_type: err instanceof Error ? err.name : typeof err, err_msg: safeErrMsg(err) })
    return Response.json(
      { detail: "Внутренняя ошибка. Попробуйте ещё раз." },
      { status: 500, headers: { "Cache-Control": "no-store" } },
    )
  }
}

function safeErrMsg(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err)
  return msg.length > 500 ? `${msg.slice(0, 500)}…` : msg
}

/** Сброс лимитов между тестами. В production не вызывается. */
export function _resetRateLimitForTests() {
  buckets.clear()
  redisClient = undefined
}
