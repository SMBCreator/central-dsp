"use client"

import { useEffect, useState } from "react"
import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import { setToken } from "@/lib/api"
import { canAccessRoute } from "@/lib/roles"
import { useMe } from "@/lib/use-me"
import { NAV_SECTIONS, PROFILE_ITEM, isItemActive, sectionOfPath, type NavItem } from "@/lib/nav"
import { isEditableTarget, useSidebar } from "@/lib/use-sidebar"
import { ThemeToggle } from "@/components/theme-toggle"

function Icon({ d, size = 16 }: { d: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true" className="shrink-0">
      <path d={d} stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

/** Пункт меню. В свёрнутом режиме — иконка 20px по центру + тултип справа (group-hover). */
function NavLink({
  item,
  active,
  collapsed,
  onClick,
}: {
  item: NavItem
  active: boolean
  collapsed: boolean
  onClick?: () => void
}) {
  const base = "group relative flex items-center rounded-md text-sm transition-colors"
  const tone = active
    ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
    : "text-muted hover:bg-sidebar-accent/60 hover:text-sidebar-foreground"
  return (
    <Link
      href={item.href}
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      title={collapsed ? item.label : undefined}
      className={`${base} ${tone} ${collapsed ? "h-10 w-10 justify-center" : "gap-2.5 px-3 py-2"}`}
    >
      <Icon d={item.icon} size={collapsed ? 20 : 16} />
      {collapsed ? (
        <span
          role="tooltip"
          className="pointer-events-none absolute left-full top-1/2 z-50 ml-2 -translate-y-1/2 whitespace-nowrap rounded-md border border-border bg-popover px-2 py-1 text-xs text-popover-foreground opacity-0 shadow-md transition-opacity duration-100 group-hover:opacity-100 group-focus-visible:opacity-100"
        >
          {item.label}
        </span>
      ) : (
        <span className="truncate">{item.label}</span>
      )}
    </Link>
  )
}

export function Sidebar({
  /** Принудительно развёрнутый вид (мобильный off-canvas). */
  forceExpanded = false,
  onNavigate,
}: {
  forceExpanded?: boolean
  onNavigate?: () => void
}) {
  const pathname = usePathname()
  const router = useRouter()
  const { collapsed: stored, toggle } = useSidebar()
  const collapsed = forceExpanded ? false : stored
  const [openSection, setOpenSection] = useState<string>(() => sectionOfPath(pathname))

  // Навигация фильтруется по матрице ролей ROUTE_ACCESS (зеркалит серверный RBAC).
  const { me } = useMe()
  const sections = NAV_SECTIONS.map((s) => ({
    ...s,
    items: me ? s.items.filter((i) => canAccessRoute(me.role, i.href, me.permissions)) : s.items,
  })).filter((s) => s.items.length > 0)

  useEffect(() => {
    setOpenSection(sectionOfPath(pathname))
  }, [pathname])

  // Хоткей Cmd/Ctrl+B — только для десктопного сайдбара, не в полях ввода.
  useEffect(() => {
    if (forceExpanded) return
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "b") {
        if (isEditableTarget(e.target)) return
        e.preventDefault()
        toggle()
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [forceExpanded, toggle])

  const logout = () => {
    setToken(null)
    router.push("/login")
  }

  return (
    <aside
      data-collapsed={collapsed || undefined}
      className={`flex h-screen shrink-0 flex-col border-r border-sidebar-border bg-sidebar transition-[width] duration-200 ease-out ${
        collapsed ? "w-16" : "w-60"
      }`}
    >
      <div className={`flex items-center py-4 ${collapsed ? "flex-col gap-3 px-2" : "justify-between px-4"}`}>
        <Link href="/" className="flex items-center gap-2.5" aria-label="CentralDSP — на дашборд">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-sidebar-primary">
            <span className="font-mono text-sm font-bold text-sidebar-primary-foreground">C</span>
          </div>
          {!collapsed ? (
            <div>
              <p className="text-sm font-semibold leading-tight text-sidebar-foreground">CentralDSP</p>
              <p className="text-[10px] leading-tight text-muted">Programmatic-платформа</p>
            </div>
          ) : null}
        </Link>
        <ThemeToggle />
      </div>

      <nav className={`flex-1 overflow-y-auto py-2 ${collapsed ? "px-3" : "px-3"}`} aria-label="Основная навигация">
        {sections.map((section, idx) => {
          const open = openSection === section.title
          const hasActive = section.items.some((i) => isItemActive(i.href, pathname))

          if (collapsed) {
            // Свёрнутый режим: заголовки секций → тонкий разделитель, все пункты видны.
            return (
              <div key={section.title} className="flex flex-col items-center gap-0.5">
                {idx > 0 ? <div className="my-2 h-px w-6 bg-sidebar-border" aria-hidden /> : null}
                {section.items.map((item) => (
                  <NavLink key={item.href} item={item} active={isItemActive(item.href, pathname)} collapsed onClick={onNavigate} />
                ))}
              </div>
            )
          }

          return (
            <div key={section.title} className="mb-1">
              <button
                type="button"
                onClick={() => setOpenSection(open ? "" : section.title)}
                aria-expanded={open}
                className={`flex w-full items-center justify-between rounded-md px-3 py-2 text-[10px] font-semibold uppercase tracking-wider transition-colors ${
                  hasActive && !open ? "text-sidebar-foreground" : "text-muted"
                } hover:bg-sidebar-accent/40 hover:text-sidebar-foreground`}
              >
                <span className="flex items-center gap-1.5">
                  {section.title}
                  {hasActive && !open ? <span className="h-1.5 w-1.5 rounded-full bg-sidebar-primary" aria-hidden /> : null}
                </span>
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 24 24"
                  fill="none"
                  aria-hidden="true"
                  className={`transition-transform duration-200 ${open ? "rotate-180" : ""}`}
                >
                  <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
              <div className={`grid transition-[grid-template-rows] duration-200 ease-out ${open ? "grid-rows-[1fr]" : "grid-rows-[0fr]"}`}>
                <div className="overflow-hidden">
                  <div className="flex flex-col gap-0.5 pb-1 pt-0.5">
                    {section.items.map((item) => (
                      <NavLink key={item.href} item={item} active={isItemActive(item.href, pathname)} collapsed={false} onClick={onNavigate} />
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )
        })}
      </nav>

      <div className={`flex flex-col gap-0.5 border-t border-sidebar-border py-3 ${collapsed ? "items-center px-3" : "px-3"}`}>
        <NavLink item={PROFILE_ITEM} active={isItemActive(PROFILE_ITEM.href, pathname)} collapsed={collapsed} onClick={onNavigate} />
        <button
          type="button"
          onClick={logout}
          title={collapsed ? "Выйти" : undefined}
          className={`group relative flex items-center rounded-md text-sm text-muted transition-colors hover:bg-sidebar-accent/60 hover:text-sidebar-foreground ${
            collapsed ? "h-10 w-10 justify-center" : "w-full gap-2.5 px-3 py-2"
          }`}
        >
          <Icon d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4m7 14l5-5-5-5m5 5H9" size={collapsed ? 20 : 16} />
          {collapsed ? (
            <span
              role="tooltip"
              className="pointer-events-none absolute left-full top-1/2 z-50 ml-2 -translate-y-1/2 whitespace-nowrap rounded-md border border-border bg-popover px-2 py-1 text-xs text-popover-foreground opacity-0 shadow-md transition-opacity duration-100 group-hover:opacity-100"
            >
              Выйти
            </span>
          ) : (
            "Выйти"
          )}
        </button>

        {!forceExpanded ? (
          <button
            type="button"
            onClick={toggle}
            aria-expanded={!collapsed}
            aria-label={collapsed ? "Развернуть меню" : "Свернуть меню"}
            title={`${collapsed ? "Развернуть" : "Свернуть"} (Ctrl/Cmd+B)`}
            className={`mt-1 flex items-center rounded-md text-xs text-muted transition-colors hover:bg-sidebar-accent/60 hover:text-sidebar-foreground ${
              collapsed ? "h-10 w-10 justify-center" : "w-full gap-2.5 px-3 py-2"
            }`}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              aria-hidden="true"
              className={`shrink-0 transition-transform duration-200 ${collapsed ? "rotate-180" : ""}`}
            >
              <path d="M15 6l-6 6 6 6" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            {!collapsed ? (
              <span className="flex flex-1 items-center justify-between">
                Свернуть
                <kbd className="rounded border border-border px-1 font-mono text-[10px]">⌘B</kbd>
              </span>
            ) : null}
          </button>
        ) : null}
      </div>
    </aside>
  )
}
