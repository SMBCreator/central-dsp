"use client"

import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import { setToken } from "@/lib/api"
import { ThemeToggle } from "@/components/theme-toggle"

const NAV_SECTIONS = [
  {
    title: "Обзор",
    items: [
      { href: "/", label: "Дашборд", icon: "M3 13h4v8H3v-8zm7-6h4v14h-4V7zm7-4h4v18h-4V3z" },
      { href: "/analytics", label: "Аналитика", icon: "M4 19h16M6 16V9m4 7V5m4 11v-5m4 5V8" },
    ],
  },
  {
    title: "Реклама",
    items: [
      { href: "/campaigns", label: "Кампании", icon: "M3 11l18-8-6 18-4-7-8-3z" },
      { href: "/creatives", label: "Креативы", icon: "M4 5h16v14H4V5zm2 10l3-4 3 3 3-4 3 5H6z" },
      { href: "/advertisers", label: "Рекламодатели", icon: "M12 3a4 4 0 110 8 4 4 0 010-8zm-7 18a7 7 0 0114 0H5z" },
    ],
  },
  {
    title: "Трафик",
    items: [
      { href: "/supply", label: "SSP-партнёры", icon: "M4 4h7v7H4V4zm9 0h7v7h-7V4zM4 13h7v7H4v-7zm9 0h7v7h-7v-7z" },
      { href: "/rtb-test", label: "RTB-тестер", icon: "M13 2L4 14h6l-1 8 9-12h-6l1-8z" },
    ],
  },
  {
    title: "Финансы и учёт",
    items: [
      { href: "/billing", label: "Биллинг", icon: "M3 7h18v10H3V7zm0 3h18M7 14h4" },
      { href: "/ord", label: "Маркировка (ОРД)", icon: "M9 12l2 2 4-4m5 2a9 9 0 11-18 0 9 9 0 0118 0z" },
    ],
  },
  {
    title: "Инструменты",
    items: [
      { href: "/pixels", label: "Пиксель и цели", icon: "M12 8a4 4 0 110 8 4 4 0 010-8zm0-6v4m0 12v4M2 12h4m12 0h4" },
      { href: "/rules", label: "Правила", icon: "M12 3l7 4v5c0 4.5-3 8-7 9-4-1-7-4.5-7-9V7l7-4z" },
      { href: "/ai", label: "AI-инструменты", icon: "M12 2l2.4 7.2L22 12l-7.6 2.8L12 22l-2.4-7.2L2 12l7.6-2.8L12 2z" },
      { href: "/users", label: "Пользователи и доступы", icon: "M17 20h5v-2a4 4 0 00-3-3.87M9 20H4v-2a4 4 0 013-3.87m6-1.13a4 4 0 10-4-4 4 4 0 004 4zm6-8a3 3 0 11-3-3" },
      { href: "/access", label: "Доступ клиентов", icon: "M16 8a4 4 0 11-8 0 4 4 0 018 0zM4 21a8 8 0 0116 0M19 8v4m2-2h-4" },
      { href: "/settings", label: "Настройки", icon: "M12 9a3 3 0 100 6 3 3 0 000-6zm7.5 3a7.5 7.5 0 01-.1 1.2l2 1.6-2 3.4-2.4-1a7.6 7.6 0 01-2 1.2L14.6 21H9.4l-.4-2.6a7.6 7.6 0 01-2-1.2l-2.4 1-2-3.4 2-1.6a7.7 7.7 0 010-2.4l-2-1.6 2-3.4 2.4 1a7.6 7.6 0 012-1.2L9.4 3h5.2l.4 2.6a7.6 7.6 0 012 1.2l2.4-1 2 3.4-2 1.6c.06.4.1.8.1 1.2z" },
    ],
  },
]

export function Sidebar() {
  const pathname = usePathname()
  const router = useRouter()

  const logout = () => {
    setToken(null)
    router.push("/login")
  }

  return (
    <aside className="flex h-screen w-60 shrink-0 flex-col border-r border-sidebar-border bg-sidebar">
      <div className="flex items-center justify-between px-4 py-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-sidebar-primary">
            <span className="font-mono text-sm font-bold text-sidebar-primary-foreground">C</span>
          </div>
          <div>
            <p className="text-sm font-semibold leading-tight text-sidebar-foreground">CentralDSP</p>
            <p className="text-[10px] leading-tight text-muted">Programmatic-платформа</p>
          </div>
        </div>
        <ThemeToggle />
      </div>

      <nav className="flex-1 overflow-y-auto px-3 py-2" aria-label="Основная навигация">
        {NAV_SECTIONS.map((section) => (
          <div key={section.title} className="mb-3">
            <p className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-muted">
              {section.title}
            </p>
            <div className="space-y-0.5">
              {section.items.map((item) => {
                const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href)
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors ${
                      active
                        ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
                        : "text-muted hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
                    }`}
                  >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                      <path d={item.icon} stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" strokeLinecap="round" />
                    </svg>
                    {item.label}
                  </Link>
                )
              })}
            </div>
          </div>
        ))}
      </nav>

      <div className="border-t border-sidebar-border px-3 py-3">
        <button
          type="button"
          onClick={logout}
          className="flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm text-muted transition-colors hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4m7 14l5-5-5-5m5 5H9" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Выйти
        </button>
      </div>
    </aside>
  )
}
