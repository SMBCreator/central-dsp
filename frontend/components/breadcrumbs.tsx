"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"

// F: хлебные крошки — человекочитаемые названия разделов
const LABELS: Record<string, string> = {
  campaigns: "Кампании",
  new: "Новая кампания",
  creatives: "Креативы",
  moderation: "Модерация",
  billing: "Финансы",
  ord: "ОРД",
  supply: "Supply",
  settings: "Настройки",
  audiences: "Аудитории",
  analytics: "Аналитика",
  users: "Пользователи",
  advertisers: "Рекламодатели",
  access: "Доступы",
  pixels: "Пиксели",
  rules: "Правила",
  "rtb-test": "RTB-тест",
  profile: "Профиль",
}

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
