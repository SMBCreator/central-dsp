"use client"

import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import { setToken } from "@/lib/api"
import { ThemeToggle } from "@/components/theme-toggle"

const NAV = [
  { href: "/", label: "Дашборд", icon: "M3 13h4v8H3v-8zm7-6h4v14h-4V7zm7-4h4v18h-4V3z" },
  { href: "/campaigns", label: "Кампании", icon: "M3 11l18-8-6 18-4-7-8-3z" },
  { href: "/creatives", label: "Креативы", icon: "M4 5h16v14H4V5zm2 10l3-4 3 3 3-4 3 5H6z" },
  { href: "/advertisers", label: "Рекламодатели", icon: "M12 3a4 4 0 110 8 4 4 0 010-8zm-7 18a7 7 0 0114 0H5z" },
  { href: "/supply", label: "SSP-партнёры", icon: "M4 4h7v7H4V4zm9 0h7v7h-7V4zM4 13h7v7H4v-7zm9 0h7v7h-7v-7z" },
  { href: "/analytics", label: "Аналитика", icon: "M4 19h16M6 16V9m4 7V5m4 11v-5m4 5V8" },
  { href: "/billing", label: "Биллинг", icon: "M3 7h18v10H3V7zm0 3h18M7 14h4" },
  { href: "/access", label: "Доступ клиентов", icon: "M16 8a4 4 0 11-8 0 4 4 0 018 0zM4 21a8 8 0 0116 0M19 8v4m2-2h-4" },
  { href: "/pixels", label: "Пиксель и цели", icon: "M12 8a4 4 0 110 8 4 4 0 010-8zm0-6v4m0 12v4M2 12h4m12 0h4" },
  { href: "/rules", label: "Правила", icon: "M12 3l7 4v5c0 4.5-3 8-7 9-4-1-7-4.5-7-9V7l7-4z" },
  { href: "/ai", label: "AI-инструменты", icon: "M12 2l2.4 7.2L22 12l-7.6 2.8L12 22l-2.4-7.2L2 12l7.6-2.8L12 2z" },
  { href: "/rtb-test", label: "RTB-тестер", icon: "M13 2L4 14h6l-1 8 9-12h-6l1-8z" },
]

export function Sidebar() {
  const pathname = usePathname()
  const router = useRouter()

  const logout = () => {
    setToken(null)
    router.push("/login")
  }

  return (
    <aside className="flex h-screen w-60 shrink-0 flex-col p-3">
      <div className="flex h-full flex-col rounded-3xl border border-border bg-card shadow-[0_1px_2px_rgba(0,0,0,0.04),0_8px_24px_-12px_rgba(0,0,0,0.08)]">
        <div className="flex items-center justify-between px-5 py-5">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-primary">
              <span className="font-mono text-sm font-bold text-primary-foreground">C</span>
            </div>
            <div>
              <p className="text-sm font-semibold leading-tight text-foreground">CentralDSP</p>
              <p className="text-[10px] leading-tight text-muted">Programmatic-платформа</p>
            </div>
          </div>
          <ThemeToggle />
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-1" aria-label="Основная навигация">
          {NAV.map((item) => {
            const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href)
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center gap-2.5 rounded-full px-3.5 py-2 text-sm transition-colors ${
                  active
                    ? "bg-primary font-medium text-primary-foreground"
                    : "text-muted hover:bg-panel hover:text-foreground"
                }`}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                  <path d={item.icon} stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" strokeLinecap="round" />
                </svg>
                {item.label}
              </Link>
            )
          })}
        </nav>

        <div className="px-3 py-3">
          <button
            type="button"
            onClick={logout}
            className="flex w-full items-center gap-2.5 rounded-full px-3.5 py-2 text-sm text-muted transition-colors hover:bg-panel hover:text-foreground"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4m7 14l5-5-5-5m5 5H9" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Выйти
          </button>
        </div>
      </div>
    </aside>
  )
}
