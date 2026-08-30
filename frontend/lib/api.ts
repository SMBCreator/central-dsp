const BASE = "/api"

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/**
 * Приводит тело ошибки FastAPI к человекочитаемой строке.
 * FastAPI на 422 отдаёт detail массивом объектов {loc, msg, type} — без обработки
 * пользователь видел бы «Ошибка 422». Здесь собираем понятный текст.
 */
function extractErrorDetail(status: number, data: unknown): string {
  const fallback = `Ошибка ${status}`
  if (!data || typeof data !== "object") return fallback
  const detail = (data as { detail?: unknown }).detail
  if (typeof detail === "string") return detail
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => {
        if (!d || typeof d !== "object") return null
        const loc = (d as { loc?: unknown[] }).loc
        const msg = (d as { msg?: string }).msg
        const field = Array.isArray(loc) ? loc.filter((x) => x !== "body").join(".") : ""
        return field ? `${field}: ${msg ?? "неверное значение"}` : (msg ?? null)
      })
      .filter(Boolean) as string[]
    if (parts.length) return parts.join("; ")
  }
  return fallback
}

function getToken(): string | null {
  if (typeof window === "undefined") return null
  return window.localStorage.getItem("dsp_token")
}

export function setToken(token: string | null) {
  if (typeof window === "undefined") return
  if (token) window.localStorage.setItem("dsp_token", token)
  else window.localStorage.removeItem("dsp_token")
}

export async function api<T = unknown>(
  path: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" }
  const token = getToken()
  if (token) headers["Authorization"] = `Bearer ${token}`

  const res = await fetch(`${BASE}${path}`, {
    method: options.method || "GET",
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  })

  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/auth/")) {
    setToken(null)
    window.location.href = "/login"
    throw new ApiError(401, "Не авторизован")
  }

  if (!res.ok) {
    let detail = `Ошибка ${res.status}`
    try {
      detail = extractErrorDetail(res.status, await res.json())
    } catch {
      // ignore
    }
    throw new ApiError(res.status, detail)
  }

  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export const fetcher = <T = unknown>(path: string) => api<T>(path)

/**
 * Приводит любое сохранённое значение ассета (asset_url / video_url / icon)
 * к URL, который реально открывается из браузера на том же origin.
 *
 * Загрузки бэкенд отдаёт из StaticFiles по пути `/uploads/<file>`, а сам
 * бэкенд в проде смонтирован под префиксом `/api` (см. vercel.json), поэтому
 * браузеру доступен путь `/api/uploads/<file>`. В dev этот же путь проксируется
 * next.config rewrites на локальный FastAPI. Абсолютные внутренние URL вида
 * `http://127.0.0.1:8000/uploads/...` (legacy-записи в БД) тоже нормализуем.
 * Внешние CDN-ссылки и data:-URL остаются как есть.
 */
export function assetUrl(raw?: string | null): string {
  if (!raw) return ""
  const v = raw.trim()
  if (!v) return ""
  // data:/blob: — рендерим напрямую (локальное превью до загрузки)
  if (v.startsWith("data:") || v.startsWith("blob:")) return v
  // уже нормализовано
  if (v.startsWith("/api/uploads/")) return v
  // относительный путь стораджа -> публичный путь через /api
  if (v.startsWith("/uploads/")) return `/api${v}`
  // абсолютный URL: если это ссылка на /uploads/<file> любого хоста
  // (в т.ч. внутренний 127.0.0.1:8000) — маппим на текущий origin через /api
  const m = v.match(/\/uploads\/[^?#]+/)
  if (m && /^https?:\/\//i.test(v)) return `/api${m[0]}`
  // внешняя CDN-ссылка / прочее — как есть
  return v
}

/** Multipart upload helper (creative assets). Returns backend JSON. */
export async function apiUpload<T = unknown>(path: string, file: File): Promise<T> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers["Authorization"] = `Bearer ${token}`
  const fd = new FormData()
  fd.append("file", file)
  const res = await fetch(`${BASE}${path}`, { method: "POST", headers, body: fd })
  if (!res.ok) {
    let detail = `Ошибка ${res.status}`
    try {
      const data = await res.json()
      if (typeof data.detail === "string") detail = data.detail
    } catch {
      // ignore
    }
    throw new ApiError(res.status, detail)
  }
  return (await res.json()) as T
}
