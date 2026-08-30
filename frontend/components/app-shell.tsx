"use client"

import { useEffect, useState, type ReactNode } from "react"
import { usePathname, useRouter } from "next/navigation"
import useSWR from "swr"
import { fetcher } from "@/lib/api"
import { isAdminRole, isAdminRoute } from "@/lib/roles"
import { Sidebar } from "@/components/sidebar"
import { FadeIn } from "@/components/amicro/fade-in"
import { NotificationsBell } from "@/components/notifications-bell"
import { Toaster } from "@/components/toaster"
import { Breadcrumbs } from "@/components/breadcrumbs"

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const [ready, setReady] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const isAuthPage = pathname === "/login" || pathname === "/register" || pathname.startsWith("/share")

  // мобильное меню закрывается при переходе на другую страницу
  useEffect(() => {
    setMenuOpen(false)
  }, [pathname])

  useEffect(() => {
    if (isAuthPage) {
      setReady(true)
      return
    }
    const token = window.localStorage.getItem("dsp_token")
    if (!token) {
      router.replace("/login")
      return
    }
    setReady(true)
  }, [isAuthPage, pathname, router])

  // Блокировка админских страниц для рядовых пользователей:
  // по прямой ссылке — редирект на дашборд
  const { data: me } = useSWR<{ role: string }>(isAuthPage ? null : "/auth/me", fetcher)
  const blocked = !!me && !isAdminRole(me.role) && isAdminRoute(pathname)
  useEffect(() => {
    if (blocked) router.replace("/")
  }, [blocked, router])

  if (isAuthPage)
    return (
      <>
        {children}
        <Toaster />
      </>
    )
  if (!ready || blocked) return null

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <Toaster />
      {/* Десктоп: сайдбар всегда виден. Мобильные: скрыт, открывается бургером */}
      <div className="hidden md:block">
        <Sidebar />
      </div>
      {menuOpen ? (
        <div className="fixed inset-0 z-50 md:hidden">
          <button
            type="button"
            aria-label="Закрыть меню"
            onClick={() => setMenuOpen(false)}
            className="absolute inset-0 bg-foreground/40"
          />
          <div className="relative h-full w-60">
            <Sidebar />
          </div>
        </div>
      ) : null}
      <main className="relative flex-1 overflow-y-auto">
        <div className="pointer-events-none sticky top-0 z-40 flex items-center justify-between px-4 pt-4 md:justify-end md:px-6">
          <button
            type="button"
            aria-label="Открыть меню"
            onClick={() => setMenuOpen(true)}
            className="pointer-events-auto flex h-9 w-9 items-center justify-center rounded-lg border border-border bg-card text-foreground md:hidden"
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
