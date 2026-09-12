/**
 * Единый источник правды для навигации: секции сайдбара и лейблы маршрутов
 * (используются и в сайдбаре, и в хлебных крошках).
 */

export type NavItem = { href: string; label: string; icon: string }
export type NavSection = { title: string; items: NavItem[] }

export const NAV_SECTIONS: NavSection[] = [
  {
    title: "Обзор",
    items: [
      { href: "/", label: "Дашборд", icon: "M3 13h4v8H3v-8zm7-6h4v14h-4V7zm7-4h4v18h-4V3z" },
      { href: "/analytics", label: "Статистика", icon: "M4 19h16M6 16V9m4 7V5m4 11v-5m4 5V8" },
    ],
  },
  {
    title: "Реклама",
    items: [
      { href: "/campaigns", label: "Кампании", icon: "M3 11l18-8-6 18-4-7-8-3z" },
      { href: "/creatives", label: "Креативы", icon: "M4 5h16v14H4V5zm2 10l3-4 3 3 3-4 3 5H6z" },
      { href: "/moderation", label: "Модерация", icon: "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" },
      { href: "/scoring", label: "Скоринг", icon: "M3 3v18h18M7 15l3-3 3 2 5-6" },
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
      {
        href: "/audiences",
        label: "Аудитории",
        icon: "M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z",
      },
      { href: "/rules", label: "Правила", icon: "M12 3l7 4v5c0 4.5-3 8-7 9-4-1-7-4.5-7-9V7l7-4z" },
      { href: "/ai", label: "AI-инструменты", icon: "M12 2l2.4 7.2L22 12l-7.6 2.8L12 22l-2.4-7.2L2 12l7.6-2.8L12 2z" },
      {
        href: "/users",
        label: "Пользователи и доступы",
        icon: "M17 20h5v-2a4 4 0 00-3-3.87M9 20H4v-2a4 4 0 013-3.87m6-1.13a4 4 0 10-4-4 4 4 0 004 4zm6-8a3 3 0 11-3-3",
      },
      { href: "/access", label: "Доступ клиентов", icon: "M16 8a4 4 0 11-8 0 4 4 0 018 0zM4 21a8 8 0 0116 0M19 8v4m2-2h-4" },
      { href: "/audit", label: "Журнал действий", icon: "M9 5h10M9 12h10M9 19h10M5 5h.01M5 12h.01M5 19h.01" },
      { href: "/admin/alerts", label: "Алерты и письма", icon: "M15 17h5l-1.4-1.4A2 2 0 0118 14.2V11a6 6 0 10-12 0v3.2c0 .5-.2 1-.6 1.4L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" },
      {
        href: "/settings",
        label: "Настройки",
        icon: "M12 9a3 3 0 100 6 3 3 0 000-6zm7.5 3a7.5 7.5 0 01-.1 1.2l2 1.6-2 3.4-2.4-1a7.6 7.6 0 01-2 1.2L14.6 21H9.4l-.4-2.6a7.6 7.6 0 01-2-1.2l-2.4 1-2-3.4 2-1.6a7.7 7.7 0 010-2.4l-2-1.6 2-3.4 2.4 1a7.6 7.6 0 012-1.2L9.4 3h5.2l.4 2.6a7.6 7.6 0 012 1.2l2.4-1 2 3.4-2 1.6c.06.4.1.8.1 1.2z",
      },
    ],
  },
]

export const PROFILE_ITEM: NavItem = {
  href: "/profile",
  label: "Профиль",
  icon: "M12 3a4 4 0 110 8 4 4 0 010-8zm-7 18a7 7 0 0114 0",
}

/** Лейблы по первому сегменту пути (и для вложенных статических сегментов). */
export const ROUTE_LABELS: Record<string, string> = {
  ...Object.fromEntries(
    NAV_SECTIONS.flatMap((s) => s.items)
      .filter((i) => i.href !== "/")
      .map((i) => [i.href.slice(1), i.label]),
  ),
  profile: PROFILE_ITEM.label,
  // Сегменты вложенного маршрута /admin/alerts — для хлебных крошек.
  admin: "Администрирование",
  alerts: "Алерты и письма",
  report: "Отчёт",
  new: "Новая кампания",
}

/**
 * Заголовки вкладок (metadata.title в layout.tsx каждого раздела; шаблон
 * «%s — CentralDSP» задан в корневом layout). Страницы клиентские, поэтому
 * title живёт в серверных layout'ах, а не в page.tsx.
 */
export const PAGE_TITLES: Record<string, string> = {
  ...ROUTE_LABELS,
  login: "Вход",
  register: "Регистрация",
  share: "Общий отчёт",
  "campaigns/new": "Новая кампания · Кампании",
  "campaigns/[id]": "Кампания",
}

export function isItemActive(href: string, pathname: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(href + "/")
}

export function sectionOfPath(pathname: string): string {
  for (const s of NAV_SECTIONS) {
    if (s.items.some((i) => isItemActive(i.href, pathname))) return s.title
  }
  return NAV_SECTIONS[0].title
}
