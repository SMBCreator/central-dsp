"use client"

import { useCallback, useEffect, useState, type ReactNode } from "react"
import { usePathname, useRouter } from "next/navigation"
import { canAccessRoute, homeRoute } from "@/lib/roles"
import { useMe } from "@/lib/use-me"
import { api } from "@/lib/api"
import { resolveShellState } from "@/lib/shell-state"
import { Sidebar } from "@/components/sidebar"
import { FadeIn } from "@/components/amicro/fade-in"
import { NotificationsBell } from "@/components/notifications-bell"
import { Toaster } from "@/components/toaster"
import { Breadcrumbs } from "@/components/breadcrumbs"
import { ShellStatus } from "@/components/shell-status"
import { MobileDrawer } from "@/components/mobile-drawer"

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const [menuOpen, setMenuOpen] = useState(false)
  const closeMenu = useCallback(() => setMenuOpen(false), [])
  const isAuthPage = pathname === "/login" || pathname === "/register" || pathname.startsWith("/share")

  // мобильное меню закрывается при переходе на другую страницу
  useEffect(() => {
    setMenuOpen(false)
  }, [pathname])

  // Источник истины об авторизации — сервер (GET /auth/me по HttpOnly-cookie).
  // В JS токена нет, поэтому «есть ли сессия» узнаём только так.
  const { me, error, mutate } = useMe(isAuthPage)

  // F-01: все исходы /auth/me именованы (lib/shell-state.ts) — пустого экрана нет.
  const state = resolveShellState({
    isAuthPage,
    me,
    error,
    // Блокировка недоступных разделов по матрице ролей (зеркалит серверный RBAC)
    routeAllowed: !!me && canAccessRoute(me.role, pathname, me.permissions),
  })

  useEffect(() => {
    if (state.kind === "unauthenticated") router.replace("/login")
    else if (state.kind === "route-forbidden" && me) router.replace(homeRoute(me.role))
  }, [state.kind, me, router])

  const retry = useCallback(() => {
    void mutate()
  }, [mutate])
  const logout = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST" })
    } catch {
      // сессии может уже не быть — всё равно уходим на /login
    }
    router.replace("/login")
  }, [router])

  // Toaster всегда смонтирован: ошибки API видны и до того, как оболочка отрендерилась (F-01).
  if (state.kind === "auth-page")
    return (
      <>
        {children}
        <Toaster />
      </>
    )
  if (state.kind === "unauthenticated" || state.kind === "route-forbidden")
    return (
      <>
        <ShellStatus state={{ kind: "loading" }} onRetry={retry} onLogout={logout} />
        <Toaster />
      </>
    )
  if (state.kind !== "ready")
    return (
      <>
        <ShellStatus state={state} onRetry={retry} onLogout={logout} />
        <Toaster />
      </>
    )

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <Toaster />
      {/* Десктоп: сайдбар expanded/collapsed (состояние в localStorage dsp.sidebar, хоткей Ctrl/Cmd+B).
          Мобильные (<md): hidden — off-canvas по бургеру, всегда развёрнутый. */}
      <div className="hidden md:block">
        <Sidebar />
      </div>
      <MobileDrawer open={menuOpen} onClose={closeMenu} label="Меню">
        <Sidebar forceExpanded onNavigate={closeMenu} />
      </MobileDrawer>
      <main className="relative flex-1 overflow-y-auto">
        <div className="pointer-events-none sticky top-0 z-40 flex items-center justify-between px-4 pt-4 md:justify-end md:px-6">
          <button
            type="button"
            aria-label="Открыть меню"
            aria-haspopup="dialog"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(true)}
            className="pointer-events-auto flex h-9 w-9 items-center justify-center rounded-lg border border-border bg-card text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 md:hidden"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M4 6h16M4 12h16M4 18h16" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
          <div className="pointer-events-auto">
            <NotificationsBell />
          </div>
        </div>
        {/* B1: единый контейнер контента для всех страниц — страницы не задают собственную ширину */}
        <FadeIn key={pathname} duration={0.35} className="mx-auto w-full max-w-[1400px] px-4 py-6 md:px-6">
          <Breadcrumbs />
          {children}
        </FadeIn>
      </main>
    </div>
  )
}
