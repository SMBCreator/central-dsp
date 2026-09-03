"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { ROUTE_LABELS } from "@/lib/nav"

// Хлебные крошки — лейблы берутся из lib/nav.ts (тот же источник, что и сайдбар)
const LABELS = ROUTE_LABELS

export function Breadcrumbs() {
  const pathname = usePathname()
  const segments = pathname.split("/").filter(Boolean)
  if (segments.length === 0) return null

  const crumbs = segments.map((seg, i) => {
    const href = "/" + segments.slice(0, i + 1).join("/")
    // Динамические сегменты (uuid/id) показываем как «Карточка»
    const label = LABELS[seg] ?? (i > 0 && !LABELS[seg] ? "Карточка" : seg)
    return { href, label, last: i === segments.length - 1 }
  })

  return (
    <nav aria-label="Хлебные крошки" className="mb-3">
      <ol className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
        <li>
          <Link href="/" className="transition-colors hover:text-foreground">
            Дашборд
          </Link>
        </li>
        {crumbs.map((c) => (
          <li key={c.href} className="flex items-center gap-1.5">
            <span aria-hidden="true">/</span>
            {c.last ? (
              <span className="text-foreground" aria-current="page">
                {c.label}
              </span>
            ) : (
              <Link href={c.href} className="transition-colors hover:text-foreground">
                {c.label}
              </Link>
            )}
          </li>
        ))}
      </ol>
    </nav>
  )
}
