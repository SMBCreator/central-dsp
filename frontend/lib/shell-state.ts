/**
 * F-01: состояние оболочки приложения как чистая функция от ответа GET /auth/me.
 * Раньше AppShell при `!me` возвращал null — любая ошибка кроме 401 давала
 * пустой экран. Здесь каждый исход именован и покрыт тестом (tests/shell-state.test.ts).
 */

export type ShellState =
  /** /login, /register, /share/* — рендерим children без оболочки и без запроса /auth/me */
  | { kind: "auth-page" }
  /** /auth/me ещё не ответил */
  | { kind: "loading" }
  /** 401 — сессии нет; AppShell уводит на /login */
  | { kind: "unauthenticated" }
  /** 403 на /auth/me — аккаунт заблокирован (auth.py current_user); выход — единственное действие */
  | { kind: "account-blocked"; detail: string }
  /** 5xx / 429 / сеть (proxy отдал 503, fetch упал) — бэкенд недоступен, показываем retry */
  | { kind: "unavailable"; status: number | null; detail: string }
  /** прочие ошибки (4xx, невалидный JSON) — тоже retry, но с честным текстом */
  | { kind: "error"; status: number | null; detail: string }
  /** пользователь есть, но раздел закрыт матрицей ролей — редирект на домашнюю страницу роли */
  | { kind: "route-forbidden" }
  | { kind: "ready" }

export interface ShellInput {
  isAuthPage: boolean
  me: unknown | null
  /** ApiError (status) или TypeError сети (без status) */
  error: unknown
  /** доступ к текущему pathname по матрице ролей; вычисляется вызывающим, когда me есть */
  routeAllowed: boolean
}

const UNAVAILABLE_TEXT = "Сервер временно недоступен. Проверьте соединение и повторите попытку."

function readStatus(error: unknown): number | null {
  if (error && typeof error === "object" && "status" in error) {
    const s = (error as { status?: unknown }).status
    if (typeof s === "number" && Number.isFinite(s)) return s
  }
  return null
}

function readMessage(error: unknown): string {
  if (error && typeof error === "object" && "message" in error) {
    const m = (error as { message?: unknown }).message
    if (typeof m === "string" && m.trim()) return m
  }
  return ""
}

export function resolveShellState(input: ShellInput): ShellState {
  if (input.isAuthPage) return { kind: "auth-page" }
  if (input.me) return input.routeAllowed ? { kind: "ready" } : { kind: "route-forbidden" }
  if (!input.error) return { kind: "loading" }

  const status = readStatus(input.error)
  const message = readMessage(input.error)
  if (status === 401) return { kind: "unauthenticated" }
  if (status === 403) return { kind: "account-blocked", detail: message || "Аккаунт заблокирован" }
  // null status = fetch не дошёл до сервера (сеть/CORS/прокси упал до ответа)
  if (status === null || status >= 500 || status === 429) {
    return { kind: "unavailable", status, detail: message || UNAVAILABLE_TEXT }
  }
  return { kind: "error", status, detail: message || `Ошибка ${status}` }
}
