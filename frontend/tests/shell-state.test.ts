import { describe, expect, it } from "vitest"
import { resolveShellState } from "@/lib/shell-state"

class ApiErrorLike extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

const me = { id: "u1", role: "admin" }

describe("resolveShellState (F-01: нет исхода с пустым экраном)", () => {
  it("страницы логина/шаринга рендерятся без оболочки независимо от ошибок", () => {
    expect(resolveShellState({ isAuthPage: true, me: null, error: new ApiErrorLike(503, "x"), routeAllowed: false })).toEqual({
      kind: "auth-page",
    })
  })

  it("нет ни me, ни ошибки — загрузка", () => {
    expect(resolveShellState({ isAuthPage: false, me: null, error: undefined, routeAllowed: false })).toEqual({ kind: "loading" })
  })

  it("401 — не авторизован (редирект на /login)", () => {
    expect(resolveShellState({ isAuthPage: false, me: null, error: new ApiErrorLike(401, "Сессия истекла"), routeAllowed: false })).toEqual({
      kind: "unauthenticated",
    })
  })

  it("403 на /auth/me — заблокированный аккаунт с текстом сервера", () => {
    expect(
      resolveShellState({ isAuthPage: false, me: null, error: new ApiErrorLike(403, "Аккаунт заблокирован"), routeAllowed: false }),
    ).toEqual({ kind: "account-blocked", detail: "Аккаунт заблокирован" })
  })

  it("5xx и 429 — сервис недоступен, retry", () => {
    expect(resolveShellState({ isAuthPage: false, me: null, error: new ApiErrorLike(503, "Ошибка 503"), routeAllowed: false })).toEqual({
      kind: "unavailable",
      status: 503,
      detail: "Ошибка 503",
    })
    expect(resolveShellState({ isAuthPage: false, me: null, error: new ApiErrorLike(429, "Слишком часто"), routeAllowed: false }).kind).toBe(
      "unavailable",
    )
  })

  it("сетевая ошибка fetch (без status) — недоступен, status=null, текст по умолчанию", () => {
    const st = resolveShellState({ isAuthPage: false, me: null, error: new TypeError("Failed to fetch"), routeAllowed: false })
    expect(st.kind).toBe("unavailable")
    if (st.kind === "unavailable") {
      expect(st.status).toBeNull()
      expect(st.detail).toBe("Failed to fetch")
    }
  })

  it("прочие 4xx — error с кодом", () => {
    expect(resolveShellState({ isAuthPage: false, me: null, error: new ApiErrorLike(422, ""), routeAllowed: false })).toEqual({
      kind: "error",
      status: 422,
      detail: "Ошибка 422",
    })
  })

  it("me есть — ready или route-forbidden по матрице ролей; ошибка SWR при наличии me игнорируется", () => {
    expect(resolveShellState({ isAuthPage: false, me, error: undefined, routeAllowed: true })).toEqual({ kind: "ready" })
    expect(resolveShellState({ isAuthPage: false, me, error: undefined, routeAllowed: false })).toEqual({ kind: "route-forbidden" })
    expect(resolveShellState({ isAuthPage: false, me, error: new ApiErrorLike(503, "stale"), routeAllowed: true })).toEqual({ kind: "ready" })
  })

  it("мусор вместо ошибки (строка, объект без status) — не падает, unavailable", () => {
    expect(resolveShellState({ isAuthPage: false, me: null, error: "boom", routeAllowed: false }).kind).toBe("unavailable")
    expect(resolveShellState({ isAuthPage: false, me: null, error: { status: "500" }, routeAllowed: false }).kind).toBe("unavailable")
  })
})
