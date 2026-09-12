"use client"

import type { ShellState } from "@/lib/shell-state"
import { Button, Spinner } from "@/components/ui"

/**
 * F-01: полноэкранные состояния оболочки вместо пустого экрана.
 * Единственный <main> на странице: AppShell в этих состояниях свой не рендерит.
 */
export function ShellStatus({
  state,
  onRetry,
  onLogout,
}: {
  state: Exclude<ShellState, { kind: "auth-page" | "ready" | "route-forbidden" | "unauthenticated" }>
  onRetry: () => void
  onLogout: () => void
}) {
  if (state.kind === "loading") {
    return (
      <main className="flex min-h-screen items-center justify-center bg-background">
        <Spinner />
      </main>
    )
  }

  const title =
    state.kind === "account-blocked"
      ? "Доступ закрыт"
      : state.kind === "unavailable"
        ? "Сервис временно недоступен"
        : "Не удалось загрузить приложение"

  const hint =
    state.kind === "account-blocked"
      ? "Обратитесь к администратору платформы, чтобы восстановить доступ."
      : state.status
        ? `Код ответа: ${state.status}`
        : "Ответ от сервера не получен."

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4">
      <section
        role="alert"
        aria-live="assertive"
        className="w-full max-w-md rounded-xl border border-border bg-card p-6 text-center shadow-sm"
      >
        <h1 className="text-lg font-semibold text-foreground">{title}</h1>
        <p className="mt-2 text-sm text-foreground">{state.detail}</p>
        <p className="mt-1 text-xs text-muted">{hint}</p>
        <div className="mt-5 flex flex-wrap justify-center gap-2">
          {state.kind === "account-blocked" ? (
            <Button onClick={onLogout}>Выйти</Button>
          ) : (
            <>
              <Button onClick={onRetry}>Повторить</Button>
              <Button variant="ghost" onClick={onLogout}>
                Войти заново
              </Button>
            </>
          )}
        </div>
      </section>
    </main>
  )
}
