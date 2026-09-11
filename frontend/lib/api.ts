const BASE = "/api"

export class ApiError extends Error {
  status: number
  /** Сырое поле `detail` из тела ответа FastAPI — для структурированных ошибок
   *  (например, 409 при запуске кампании: `{message, blockers: [...]}`). */
  detail: unknown
  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

/** Текст для `409 ord_locked` (B13a): юр. поля креатива заморожены после выдачи erid. */
export const ORD_LOCKED_MESSAGE =
  "Данные ОРД зафиксированы после запуска кампании: контрагент, договор и ККТУ креатива с erid менять нельзя. Для другого заказчика или договора создайте новый креатив."

/** Человекочитаемое сообщение об ошибке API с учётом структурированных кодов ОРД. */
export function describeApiError(err: unknown, fallback = "Ошибка"): string {
  if (err instanceof ApiError) {
    const d = err.detail as { code?: string } | undefined
    if (d && typeof d === "object" && d.code === "ord_locked") return ORD_LOCKED_MESSAGE
    return err.message
  }
  return err instanceof Error ? err.message : fallback
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
  // Структурированная ошибка бэкенда: {message: string, ...}
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const msg = (detail as { message?: unknown }).message
    if (typeof msg === "string" && msg) return msg
  }
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

/**
 * Сессия — только HttpOnly-cookie `cdsp_session` (same-origin через /api-прокси).
 * В JS токена нет вообще: ни в localStorage, ни в заголовке Authorization.
 * Авторизованность определяется ответом GET /auth/me (lib/use-me.ts).
 */

/** Ключ localStorage, оставшийся от старой схемы с Bearer — вычищаем при загрузке. */
const LEGACY_TOKEN_KEY = "dsp_token"
if (typeof window !== "undefined") {
  try {
    window.localStorage.removeItem(LEGACY_TOKEN_KEY)
  } catch {
    // приватный режим / запрет storage — не критично
  }
}

let mockWarned = false
/** Демо-ответ прокси (x-dsp-mock) — предупреждаем один раз, чтобы фейковые цифры не приняли за реальные. */
function warnIfMock(res: Response) {
  if (mockWarned || typeof window === "undefined") return
  if (res.headers.get("x-dsp-mock") === "1") {
    mockWarned = true
    console.warn("[Central DSP] Бэкенд недоступен — интерфейс показывает демо-данные (mock).")
  }
}

export async function api<T = unknown>(
  path: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" }

  const res = await fetch(`${BASE}${path}`, {
    method: options.method || "GET",
    headers,
    credentials: "same-origin",
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  })
  warnIfMock(res)

  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/auth/")) {
    // H5: не молчаливый фейл — сообщение на /login
    window.location.href = "/login?reason=session_expired"
    throw new ApiError(401, "Сессия истекла — войдите заново")
  }

  if (!res.ok) {
    let detail = `Ошибка ${res.status}`
    let rawDetail: unknown
    try {
      const data = await res.json()
      rawDetail = data && typeof data === "object" ? (data as { detail?: unknown }).detail : undefined
      detail = extractErrorDetail(res.status, data)
    } catch {
      // ignore
    }
    // F5: единый toast-обработчик — ни одна ошибка API не проглатывается молча.
    // Динамический импорт, чтобы lib/api.ts оставался импортируемым из любого контекста.
    // Исключение: 401 на /auth/me — это штатная проверка «есть ли сессия»
    // (AppShell уводит на /login), тост здесь только шумит.
    const silent = res.status === 401 && path === "/auth/me"
    if (typeof window !== "undefined" && !silent) {
      import("@/components/toaster").then(({ toastError }) => toastError(detail)).catch(() => {})
    }
    throw new ApiError(res.status, detail, rawDetail)
  }

  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export const fetcher = <T = unknown>(path: string) => api<T>(path)

/**
 * Скачивание файла (CSV-экспорт): качаем через fetch (cookie уходит сама),
 * чтобы показать ошибку бэкенда вместо «битого» файла, и отдаём как blob.
 */
export async function downloadCsv(path: string, filename: string): Promise<void> {
  const res = await fetch(`${BASE}${path}`, { credentials: "same-origin" })
  if (!res.ok) {
    const detail = `Не удалось выгрузить файл (${res.status})`
    if (typeof window !== "undefined") {
      import("@/components/toaster").then(({ toastError }) => toastError(detail)).catch(() => {})
    }
    throw new ApiError(res.status, detail)
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

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

export interface UploadOptions {
  /** 0..1 — прогресс отправки тела (только upload-часть, без обработки на сервере) */
  onProgress?: (fraction: number) => void
  /** Таймаут в мс; по умолчанию 60 с для картинок и 5 мин для видео */
  timeoutMs?: number
  signal?: AbortSignal
}

export interface UploadResult {
  url: string
  path: string
  size: number
  mime?: string
  width?: number | null
  height?: number | null
}

/**
 * Multipart upload (файлы креативов) через XMLHttpRequest — fetch не даёт
 * прогресс отправки. Ошибки бэкенда ({detail}) пробрасываются как ApiError.
 */
export function apiUpload<T = UploadResult>(path: string, file: File, opts: UploadOptions = {}): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const isVideo = file.type.startsWith("video/")
    xhr.timeout = opts.timeoutMs ?? (isVideo ? 5 * 60_000 : 60_000)
    xhr.open("POST", `${BASE}${path}`)
    xhr.withCredentials = true

    const fail = (status: number, detail: string) => {
      if (typeof window !== "undefined") {
        import("@/components/toaster").then(({ toastError }) => toastError(detail)).catch(() => {})
      }
      reject(new ApiError(status, detail))
    }

    xhr.upload.onprogress = (e) => {
      if (opts.onProgress && e.lengthComputable) opts.onProgress(e.loaded / e.total)
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          opts.onProgress?.(1)
          resolve(JSON.parse(xhr.responseText) as T)
        } catch {
          fail(xhr.status, "Некорректный ответ сервера")
        }
        return
      }
      let detail = xhr.status === 413 ? "Файл слишком большой для сервера" : `Ошибка ${xhr.status}`
      try {
        detail = extractErrorDetail(xhr.status, JSON.parse(xhr.responseText))
      } catch {
        // ignore
      }
      fail(xhr.status, detail)
    }
    xhr.onerror = () => fail(0, "Сетевая ошибка при загрузке файла")
    xhr.ontimeout = () => fail(0, "Превышено время ожидания загрузки")
    xhr.onabort = () => reject(new ApiError(0, "Загрузка отменена"))
    opts.signal?.addEventListener("abort", () => xhr.abort(), { once: true })

    const fd = new FormData()
    fd.append("file", file)
    xhr.send(fd)
  })
}

/** Удалить «висящий» загруженный файл (если создание креатива не состоялось). Ошибки глушим. */
export async function apiDeleteUpload(path: string | null | undefined): Promise<void> {
  const m = (path || "").match(/\/uploads\/([0-9a-f]{32}\.[a-z0-9]+)/)
  if (!m) return
  try {
    await api(`/creatives/upload/${m[1]}`, { method: "DELETE" })
  } catch {
    // best effort
  }
}
