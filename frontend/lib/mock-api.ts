/**
 * Demo-данные для превью v0 (песочница без Python-бэкенда и Postgres).
 *
 * Используются ТОЛЬКО как fallback в app/api/[...path]/route.ts, когда реальный
 * FastAPI недоступен. В проде vercel.json направляет /api на Python-бэкенд до
 * Next.js, поэтому этот модуль в прод-трафике не участвует.
 */

import type { ScoringProfile } from "@/lib/types"

const now = Date.now()
const iso = (msAgo: number) => new Date(now - msAgo).toISOString()
const today = new Date().toISOString().slice(0, 10)

export const mockAdvertisers = [
  {
    id: "adv-1",
    display_id: 101,
    name: "ООО «Северный Свет»",
    domain: "severny-svet.ru",
    category: "IAB18",
    status: "active",
    balance: 124_500,
    currency: "RUB",
    created_at: iso(90 * 864e5),
  },
  {
    id: "adv-2",
    display_id: 102,
    name: "Кофейня «Дербент»",
    domain: "derbent.coffee",
    category: "IAB8",
    status: "active",
    balance: 8_200,
    currency: "RUB",
    created_at: iso(45 * 864e5),
  },
  {
    id: "adv-3",
    display_id: 103,
    name: "Фитнес-клуб «Пульс»",
    domain: "pulse.fit",
    category: "IAB7",
    status: "active",
    balance: 0,
    currency: "RUB",
    created_at: iso(20 * 864e5),
  },
]

export const mockGroups = [
  { id: "grp-1", name: "Осенняя распродажа", created_at: iso(30 * 864e5) },
  { id: "grp-2", name: "Брендинг", created_at: iso(60 * 864e5) },
]

export const mockCampaigns = [
  {
    id: "cmp-1",
    display_id: 1001,
    advertiser_id: "adv-1",
    group_id: "grp-1",
    name: "Осень-2026 — куртки и пальто",
    objective: "traffic",
    status: "active",
    daily_budget: 15_000,
    total_budget: 450_000,
    spent_today: 6_320.4,
    spent_total: 187_450.75,
    start_date: today,
    end_date: null,
    pacing: "smooth",
    bid_shading: true,
    frequency_cap_impressions: 3,
    frequency_cap_period_hours: 24,
    frequency_caps: [{ impressions: 3, period_hours: 24 }],
    kktu_code: "22.1.1",
    kktu_label: null,
    created_at: iso(35 * 864e5),
  },
  {
    id: "cmp-2",
    display_id: 1002,
    advertiser_id: "adv-2",
    group_id: null,
    name: "Новое меню — гео Москва",
    objective: "awareness",
    status: "paused",
    daily_budget: 4_000,
    total_budget: 80_000,
    spent_today: 0,
    spent_total: 12_940.1,
    start_date: today,
    end_date: null,
    pacing: "asap",
    bid_shading: false,
    frequency_cap_impressions: null,
    frequency_cap_period_hours: null,
    frequency_caps: null,
    kktu_code: "8.1.2",
    kktu_label: null,
    created_at: iso(12 * 864e5),
  },
  {
    id: "cmp-3",
    display_id: 1003,
    advertiser_id: "adv-3",
    group_id: "grp-2",
    name: "Абонементы — январь",
    objective: "conversions",
    status: "active",
    daily_budget: 10_000,
    total_budget: 200_000,
    spent_today: 0,
    spent_total: 0,
    start_date: today,
    end_date: null,
    pacing: "smooth",
    bid_shading: true,
    clean_traffic_only: true,
    frequency_cap_impressions: 2,
    frequency_cap_period_hours: 12,
    frequency_caps: [{ impressions: 2, period_hours: 12 }],
    kktu_code: null,
    kktu_label: null,
    created_at: iso(5 * 864e5),
  },
  {
    id: "cmp-4",
    display_id: 1004,
    advertiser_id: "adv-1",
    group_id: null,
    name: "Черновик — зимняя коллекция",
    objective: "traffic",
    status: "draft",
    daily_budget: 0,
    total_budget: 0,
    spent_today: 0,
    spent_total: 0,
    start_date: null,
    end_date: null,
    pacing: "smooth",
    bid_shading: false,
    frequency_cap_impressions: null,
    frequency_cap_period_hours: null,
    frequency_caps: null,
    kktu_code: null,
    kktu_label: null,
    created_at: iso(1 * 864e5),
  },
]

export const mockLineItems = [
  {
    id: "li-1",
    display_id: 2001,
    campaign_id: "cmp-1",
    name: "Баннеры — RU, десктоп",
    status: "active",
    bid_type: "cpm",
    bid_price: 120,
    max_bid: 250,
    daily_budget: 8_000,
    spent_today: 3_820.4,
    targeting: { geo_countries: ["RU"], device_types: ["desktop"] },
    created_at: iso(35 * 864e5),
  },
  {
    id: "li-2",
    display_id: 2002,
    campaign_id: "cmp-1",
    name: "Натив — мобайл",
    status: "active",
    bid_type: "cpm",
    bid_price: 90,
    max_bid: null,
    daily_budget: 7_000,
    spent_today: 2_500,
    targeting: { geo_countries: ["RU"], device_types: ["mobile"] },
    created_at: iso(34 * 864e5),
  },
  {
    id: "li-3",
    display_id: 2003,
    campaign_id: "cmp-3",
    name: "Видео — все устройства",
    status: "paused",
    bid_type: "cpm",
    bid_price: 0,
    max_bid: null,
    daily_budget: 10_000,
    spent_today: 0,
    targeting: { geo_countries: ["RU"] },
    created_at: iso(5 * 864e5),
  },
]

export const mockCreatives = [
  {
    id: "cr-1",
    display_id: 3001,
    campaign_id: "cmp-1",
    line_item_id: "li-1",
    name: "Куртки 970×250",
    type: "banner",
    status: "approved",
    width: 970,
    height: 250,
    asset_url: "/placeholder.svg?height=250&width=970",
    click_url: "https://severny-svet.ru/autumn",
    html_snippet: null,
    native_title: null,
    native_description: null,
    native_cta: null,
    native_icon_url: null,
    video_url: null,
    video_duration: null,
    vast_xml: null,
    counterparty_id: "cp-1",
    kktu_code: "22.1.1",
    kktu_label: "Одежда и обувь",
    erid: "2VtzqwHijkL",
    moderation_comment: null,
    created_at: iso(30 * 864e5),
  },
  {
    id: "cr-2",
    display_id: 3002,
    campaign_id: "cmp-1",
    line_item_id: "li-2",
    name: "Натив — пальто",
    type: "native",
    status: "pending",
    width: null,
    height: null,
    asset_url: "/placeholder.svg?height=314&width=600",
    click_url: "https://severny-svet.ru/coats",
    html_snippet: null,
    native_title: "Пальто из новой коллекции",
    native_description: "Тёплые пальто со скидкой до 30% только до конца недели",
    native_cta: "Смотреть",
    native_icon_url: "/placeholder.svg?height=80&width=80",
    video_url: null,
    video_duration: null,
    vast_xml: null,
    counterparty_id: "cp-1",
    kktu_code: "22.1.1",
    kktu_label: "Одежда и обувь",
    erid: null,
    moderation_comment: null,
    created_at: iso(8 * 864e5),
  },
  {
    id: "cr-3",
    display_id: 3003,
    campaign_id: "cmp-2",
    line_item_id: null,
    name: "Новое меню 300×250",
    type: "banner",
    status: "rejected",
    width: 300,
    height: 250,
    asset_url: "/placeholder.svg?height=250&width=300",
    click_url: "https://derbent.coffee/menu",
    html_snippet: null,
    native_title: null,
    native_description: null,
    native_cta: null,
    native_icon_url: null,
    video_url: null,
    video_duration: null,
    vast_xml: null,
    counterparty_id: null,
    kktu_code: null,
    kktu_label: null,
    erid: null,
    moderation_comment: "Не указан контрагент для маркировки ОРД",
    created_at: iso(3 * 864e5),
  },
]

// Поля — как в backend/routers/ord_router.py (CounterpartyIn / ContractIn + ord_status/ord_error)
export const mockCounterparties = [
  {
    id: "cp-1",
    name: "ООО «Северный Свет»",
    inn: "7810123456",
    ogrn: "1027810123456",
    legal_type: "ul",
    role: "advertiser",
    advertiser_id: "adv-1",
    address: "г. Санкт-Петербург, Невский пр., д. 1",
    ord_status: "registered",
    ord_error: null,
    ord_external_id: "cp-ord-1",
    created_at: iso(60 * 864e5),
  },
  {
    id: "cp-2",
    name: "ООО «ЦентралДСП»",
    inn: "7707083893",
    ogrn: "1027700132195",
    legal_type: "ul",
    role: "ors",
    advertiser_id: null,
    address: "г. Москва, ул. Тверская, д. 7",
    ord_status: "registered",
    ord_error: null,
    ord_external_id: "cp-ord-2",
    created_at: iso(90 * 864e5),
  },
  {
    id: "cp-3",
    name: "ИП Петров А.В.",
    inn: "781012345678",
    ogrn: "318784700012345",
    legal_type: "ip",
    role: "advertiser",
    advertiser_id: "adv-2",
    address: "г. Санкт-Петербург, Литейный пр., д. 22",
    ord_status: "draft",
    ord_error: null,
    ord_external_id: null,
    created_at: iso(3 * 864e5),
  },
]

export const mockContracts = [
  {
    id: "ct-1",
    client_id: "cp-1",
    contractor_id: "cp-2",
    client_name: "ООО «Северный Свет»",
    contractor_name: "ООО «ЦентралДСП»",
    contract_type: "service",
    subject_type: "distribution",
    number: "ДС-2026/14",
    signed_at: "2026-01-15",
    amount: 450_000,
    is_vat: true,
    ord_status: "registered",
    ord_error: null,
    ord_external_id: "ct-ord-1",
    created_at: iso(50 * 864e5),
  },
  {
    id: "ct-2",
    client_id: "cp-3",
    contractor_id: "cp-2",
    client_name: "ИП Петров А.В.",
    contractor_name: "ООО «ЦентралДСП»",
    contract_type: "service",
    subject_type: "distribution",
    number: "ДС-2026/21",
    signed_at: "2026-08-30",
    amount: 120_000,
    is_vat: false,
    ord_status: "failed",
    ord_error: "Заказчик не зарегистрирован в ОРД — сначала отправьте контрагента",
    ord_external_id: null,
    created_at: iso(2 * 864e5),
  },
]

function seriesPoint(i: number, hours: number) {
  const wave = Math.sin((i / hours) * Math.PI)
  const bids = Math.round(4200 + wave * 2600 + (i % 7) * 90)
  const wins = Math.round(bids * (0.24 + wave * 0.05))
  const impressions = Math.round(wins * 0.96)
  const clicks = Math.round(impressions * 0.012)
  const spend = Math.round(impressions * 0.11 * 100) / 100
  return { ts: iso((hours - i) * 36e5), bids, wins, impressions, clicks, spend }
}

export function mockTimeseries(hours: number, granularity: "hour" | "day" | "week" = "hour") {
  const pts = []
  for (let i = 1; i <= hours; i++) pts.push(seriesPoint(i, hours))
  if (granularity === "hour") return pts
  // Агрегация как date_trunc на бэке: по началу дня / ISO-недели
  const buckets = new Map<string, (typeof pts)[number]>()
  for (const p of pts) {
    const d = new Date(p.ts)
    d.setMinutes(0, 0, 0)
    d.setHours(0)
    if (granularity === "week") {
      const dow = (d.getDay() + 6) % 7 // понедельник = 0
      d.setDate(d.getDate() - dow)
    }
    const key = d.toISOString()
    const acc = buckets.get(key)
    if (acc) {
      acc.bids += p.bids
      acc.wins += p.wins
      acc.impressions += p.impressions
      acc.clicks += p.clicks
      acc.spend = Math.round((acc.spend + p.spend) * 100) / 100
    } else {
      buckets.set(key, { ...p, ts: key })
    }
  }
  return Array.from(buckets.values()).sort((a, b) => a.ts.localeCompare(b.ts))
}

export function mockSummary(hours: number, campaignId?: string | null) {
  const scale = campaignId ? 0.45 : 1
  const pts = mockTimeseries(hours)
  const bids = Math.round(pts.reduce((s, p) => s + p.bids, 0) * scale)
  const wins = Math.round(pts.reduce((s, p) => s + p.wins, 0) * scale)
  const impressions = Math.round(pts.reduce((s, p) => s + p.impressions, 0) * scale)
  const clicks = Math.round(pts.reduce((s, p) => s + p.clicks, 0) * scale)
  const spend = Math.round(pts.reduce((s, p) => s + p.spend, 0) * scale * 100) / 100
  return {
    bids,
    wins,
    impressions,
    clicks,
    spend,
    win_rate: bids ? Math.round((wins / bids) * 10000) / 100 : 0,
    ctr: impressions ? Math.round((clicks / impressions) * 10000) / 100 : 0,
    ecpm: impressions ? Math.round((spend / impressions) * 1000 * 100) / 100 : 0,
  }
}

/** Диагностика показов — та же структура, что отдаёт GET /campaigns/{id}/diagnostics */
export function mockDiagnostics(campaignId: string) {
  const camp = mockCampaigns.find((c) => c.id === campaignId) ?? mockCampaigns[0]
  const adv = mockAdvertisers.find((a) => a.id === camp.advertiser_id)
  const lis = mockLineItems.filter((l) => l.campaign_id === camp.id)
  const liActive = lis.filter((l) => l.status === "active" && l.bid_price > 0)
  const crs = mockCreatives.filter((c) => c.campaign_id === camp.id)
  const crApproved = crs.filter((c) => c.status === "approved")
  const unmarked = crApproved.filter((c) => !c.erid)

  const checks = [
    {
      code: "campaign_status",
      ok: camp.status === "active",
      severity: "blocker",
      title: "Статус кампании",
      detail:
        camp.status === "active"
          ? "Кампания активна"
          : `Кампания в статусе «${camp.status}». Запустите её, чтобы участвовать в торгах.`,
    },
    {
      code: "schedule",
      ok: true,
      severity: "blocker",
      title: "Период открутки",
      detail: "Сегодня входит в период кампании",
    },
    {
      code: "total_budget",
      ok: !camp.total_budget || camp.spent_total < camp.total_budget,
      severity: "blocker",
      title: "Общий бюджет",
      detail:
        !camp.total_budget || camp.spent_total < camp.total_budget
          ? "Общий бюджет не исчерпан"
          : "Израсходован весь общий бюджет. Увеличьте бюджет.",
    },
    {
      code: "daily_budget",
      ok: !camp.daily_budget || camp.spent_today < camp.daily_budget,
      severity: "warning",
      title: "Дневной бюджет",
      detail:
        !camp.daily_budget || camp.spent_today < camp.daily_budget
          ? "Дневной бюджет не исчерпан"
          : "Дневной бюджет на сегодня израсходован. Показы возобновятся после сброса в 00:00 МСК.",
    },
    {
      code: "advertiser_status",
      ok: adv?.status === "active",
      severity: "blocker",
      title: "Рекламодатель",
      detail: adv?.status === "active" ? "Рекламодатель активен" : "Рекламодатель не активен.",
    },
    {
      code: "balance",
      ok: (adv?.balance ?? 0) > 0,
      severity: "blocker",
      title: "Баланс",
      detail:
        (adv?.balance ?? 0) > 0
          ? `Баланс рекламодателя: ${(adv?.balance ?? 0).toFixed(2)} ₽`
          : "Баланс рекламодателя 0 ₽ — торги остановлены. Пополните баланс в разделе «Финансы».",
    },
    {
      code: "line_items",
      ok: liActive.length > 0,
      severity: "blocker",
      title: "Группы объявлений",
      detail:
        liActive.length > 0
          ? `Активных групп со ставкой > 0: ${liActive.length}`
          : lis.length === 0
            ? "Нет ни одной группы объявлений — создайте группу с таргетингом и ставкой."
            : "Все группы на паузе или со ставкой 0 ₽. Активируйте группу и задайте ставку.",
    },
    {
      code: "creatives",
      ok: crApproved.length > 0,
      severity: "blocker",
      title: "Одобренные креативы",
      detail:
        crApproved.length > 0
          ? `Одобренных креативов: ${crApproved.length}`
          : crs.length === 0
            ? "Нет ни одного креатива — загрузите креативы в кампанию."
            : "Ни один креатив не одобрен. Дождитесь модерации.",
    },
    {
      code: "ord_marking",
      ok: unmarked.length === 0,
      severity: "blocker",
      title: "Маркировка ОРД (erid)",
      detail:
        unmarked.length === 0
          ? "Все одобренные креативы промаркированы"
          : `${unmarked.length} одобренных креатив(ов) без erid. Зарегистрируйте их в ОРД (38-ФЗ).`,
    },
    {
      code: "optimizer_blocks",
      ok: true,
      severity: "warning",
      title: "Заблокированные источники",
      detail: "Оптимизатор не блокировал источники",
    },
  ]

  const blockers = checks.filter((c) => !c.ok && c.severity === "blocker")
  const warnings = checks.filter((c) => !c.ok && c.severity === "warning")
  return {
    eligible: blockers.length === 0,
    summary: blockers.length
      ? "Кампания не откручивается: устраните блокирующие проблемы ниже."
      : warnings.length
        ? "Кампания участвует в торгах, но охват ограничен — см. предупреждения."
        : "Все проверки пройдены — кампания готова к откруткам.",
    blocker_count: blockers.length,
    warning_count: warnings.length,
    checks,
  }
}

export const mockBillingSummary = {
  advertisers: mockAdvertisers.map((a) => ({
    id: a.id,
    name: a.name,
    status: a.status,
    balance: a.balance,
    currency: a.currency,
  })),
  total_deposited: 380_000,
  total_spent: 200_390.85,
  total_adjusted: 0,
}

export const mockTransactions = [
  {
    id: 1,
    advertiser_id: "adv-1",
    advertiser_name: "ООО «Северный Свет»",
    type: "deposit",
    amount: 300_000,
    balance_after: 300_000,
    description: "Пополнение по счёту №112",
    created_at: iso(40 * 864e5),
  },
  {
    id: 2,
    advertiser_id: "adv-2",
    advertiser_name: "Кофейня «Дербент»",
    type: "deposit",
    amount: 80_000,
    balance_after: 80_000,
    description: "Пополнение по счёту №118",
    created_at: iso(20 * 864e5),
  },
  {
    id: 3,
    advertiser_id: "adv-1",
    advertiser_name: "ООО «Северный Свет»",
    type: "spend",
    amount: -187_450.75,
    balance_after: 124_500,
    description: "Открутка кампании «Осень-2026»",
    created_at: iso(1 * 864e5),
  },
]

export const mockUser = { id: "u-1", email: "demo@central-dsp.ru", name: "Демо-пользователь", role: "admin" }

/**
 * Разбирает запрос и возвращает mock-ответ.
 * Возвращает undefined, если путь неизвестен (обработчик отдаст пустой ответ по методу).
 */
// Ручные блоки площадок в мок-режиме — живут в памяти процесса (сбрасываются при рестарте).
const mockManualBlocks = new Set<string>()

type MockRow = { id: string; [k: string]: unknown }

// Пустые в демо по умолчанию, но создаваемые из UI сущности живут в памяти процесса.
const mockAudiences: MockRow[] = []
const mockRules: MockRow[] = []
const mockUsers: MockRow[] = [mockUser as MockRow]

/**
 * Универсальный in-memory CRUD для демо-коллекций: POST добавляет, PATCH/PUT
 * обновляет, DELETE удаляет — чтобы кабинет в превью вёл себя как с настоящим
 * бэком (созданная сущность появляется в списке, удалённая исчезает).
 * Возвращает undefined, если запрос не относится к коллекции.
 */
function crud<T extends MockRow>(
  method: string,
  path: string,
  body: unknown,
  base: string,
  rows: T[],
  opts: { prefix: string; defaults?: (b: Partial<T>) => Partial<T> } ,
): unknown {
  const b = (body && typeof body === "object" ? body : {}) as Partial<T>
  if (path === base && method === "POST") {
    const template = (rows[0] || {}) as T
    const created = {
      ...template,
      ...(opts.defaults ? opts.defaults(b) : {}),
      ...b,
      id: `${opts.prefix}-${Date.now().toString(36)}`,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    } as T
    rows.unshift(created)
    return created
  }
  const m = path.match(new RegExp(`^${base.replace(/\//g, "\\/")}\\/([^/]+)$`))
  if (!m) return undefined
  const idx = rows.findIndex((r) => r.id === m[1])
  if (method === "GET") return idx >= 0 ? rows[idx] : undefined
  if (method === "PATCH" || method === "PUT") {
    if (idx < 0) return undefined
    rows[idx] = { ...rows[idx], ...b, id: m[1], updated_at: new Date().toISOString() } as T
    return rows[idx]
  }
  if (method === "DELETE") {
    if (idx >= 0) rows.splice(idx, 1)
    return { ok: true }
  }
  return undefined
}

export function resolveMock(method: string, path: string, search: URLSearchParams, body?: unknown): unknown {
  // --- Auth ---
  if (path === "/auth/login" || path === "/auth/register") return { token: "demo-token" }
  if (path === "/auth/me" || path === "/users/me") return mockUser
  if (path === "/auth/change-password") return { ok: true }

  // --- Кампании ---
  if (path === "/campaigns" && method === "GET") return mockCampaigns
  const diagMatch = path.match(/^\/campaigns\/([^/]+)\/diagnostics$/)
  if (diagMatch) return mockDiagnostics(diagMatch[1])
  const blocksMatch = path.match(/^\/campaigns\/([^/]+)\/blocks$/)
  if (blocksMatch) return []
  const dupMatch = path.match(/^\/campaigns\/([^/]+)\/duplicate$/)
  if (dupMatch && method === "POST") {
    const src = mockCampaigns.find((c) => c.id === dupMatch[1]) ?? mockCampaigns[0]
    const copy = {
      ...src,
      id: `${src.id}-copy-${Date.now().toString(36).slice(-4)}`,
      display_id: 1000 + mockCampaigns.length,
      name: `${src.name} (копия)`,
      status: "draft",
      spent_today: 0,
      spent_total: 0,
    }
    mockCampaigns.unshift(copy)
    return copy
  }
  const campMatch = path.match(/^\/campaigns\/([^/]+)$/)
  if (campMatch && method === "GET") {
    return mockCampaigns.find((c) => c.id === campMatch[1]) ?? mockCampaigns[0]
  }
  const campCrud = crud(method, path, body, "/campaigns", mockCampaigns as MockRow[], {
    prefix: "cmp",
    defaults: () => ({
      display_id: 1000 + mockCampaigns.length,
      status: "draft",
      spent_today: 0,
      spent_total: 0,
    }),
  })
  if (campCrud !== undefined) return campCrud

  // --- Line items: привязка креативов (G9) ---
  const liCrMatch = path.match(/^\/line-items\/([^/]+)\/creatives$/)
  if (liCrMatch) return { line_item_id: liCrMatch[1], creative_ids: [] }

  // --- Статистика ---
  if (path === "/stats/summary")
    return mockSummary(Number(search.get("hours") || 24), search.get("campaign_id"))
  if (path === "/stats/timeseries") {
    const gran = (search.get("granularity") || "hour") as "hour" | "day" | "week"
    const dFrom = search.get("date_from")
    const dTo = search.get("date_to")
    let h = Math.min(Number(search.get("hours") || 24), 720)
    if (dFrom && dTo) {
      const days = Math.round((new Date(dTo).getTime() - new Date(dFrom).getTime()) / 864e5) + 1
      h = Math.min(Math.max(days, 1) * 24, 720)
    }
    return mockTimeseries(h, gran)
  }
  if (path === "/stats/report") {
    // Демо-отчёт по дням: форма ответа совпадает с бэком ({rows, totals})
    const days = 7
    const rows = Array.from({ length: days }, (_, i) => {
      const d = new Date()
      d.setDate(d.getDate() - (days - 1 - i))
      const imp = 12000 + Math.round(Math.sin(i * 1.7) * 3500) + i * 900
      const clk = Math.round(imp * (0.011 + 0.002 * Math.sin(i)))
      const spend = Math.round(imp * 0.14 * 100) / 100
      const conv = Math.round(clk * 0.06)
      const viewable = Math.round(imp * (0.62 + 0.08 * Math.sin(i * 2.1)))
      return {
        day: d.toISOString().slice(0, 10),
        impressions: imp,
        clicks: clk,
        ctr: imp ? Math.round((clk / imp) * 100 * 1000) / 1000 : 0,
        spend,
        conversions: conv,
        bids: Math.round(imp * 1.8),
        wins: imp,
        win_rate: Math.round((55 + 5 * Math.sin(i)) * 100) / 100,
        cpm: imp ? (spend / imp) * 1000 : 0,
        cpc: clk ? spend / clk : 0,
        cpa: conv ? spend / conv : 0,
        viewable_imps: viewable,
        viewability: imp ? Math.round((viewable / imp) * 100 * 10) / 10 : 0,
      }
    })
    const totals = rows.reduce(
      (acc, r) => {
        acc.impressions += r.impressions
        acc.clicks += r.clicks
        acc.spend = Math.round((acc.spend + r.spend) * 100) / 100
        acc.conversions += r.conversions
        acc.bids += r.bids
        acc.wins += r.wins
        acc.viewable_imps += r.viewable_imps
        return acc
      },
      { impressions: 0, clicks: 0, spend: 0, conversions: 0, bids: 0, wins: 0, viewable_imps: 0 } as Record<
        string,
        number
      >,
    )
    totals.ctr = totals.impressions ? Math.round((totals.clicks / totals.impressions) * 100 * 1000) / 1000 : 0
    totals.win_rate = totals.bids ? Math.round((totals.wins / totals.bids) * 100 * 100) / 100 : 0
    totals.cpm = totals.impressions ? (totals.spend / totals.impressions) * 1000 : 0
    totals.cpc = totals.clicks ? totals.spend / totals.clicks : 0
    totals.cpa = totals.conversions ? totals.spend / totals.conversions : 0
    totals.viewability = totals.impressions
      ? Math.round((totals.viewable_imps / totals.impressions) * 100 * 10) / 10
      : 0
    return { rows, totals }
  }
  if (path.startsWith("/stats/breakdown")) {
    // Строки разбивки выводим из spent_total кампаний, чтобы цифры в списке
    // кампаний/групп были согласованы с карточками (eCPM ~ 180 ₽, CTR ~ 0.8%).
    const dimension = search.get("dimension") || "campaign"
    const rowFor = (id: string, name: string, status: string, spend: number) => {
      const impressions = Math.round((spend / 180) * 1000)
      const clicks = Math.round(impressions * 0.0082)
      const wins = impressions
      const bids = Math.round(wins / 0.31)
      return {
        id,
        name,
        status,
        bids,
        wins,
        impressions,
        clicks,
        viewable_imps: Math.round(impressions * 0.71),
        spend,
        // Как в backend/routers/stats.py: win_rate и ctr — в процентах.
        win_rate: bids ? Math.round((wins / bids) * 10000) / 100 : 0,
        ctr: impressions ? Math.round((clicks / impressions) * 100000) / 1000 : 0,
        ecpm: impressions ? Math.round((spend / impressions) * 1000 * 100) / 100 : 0,
      }
    }
    if (dimension === "group") {
      return mockGroups.map((g) => {
        const spend = mockCampaigns.filter((c) => c.group_id === g.id).reduce((s, c) => s + (c.spent_total || 0), 0)
        return rowFor(g.id, g.name, "active", spend)
      })
    }
    if (dimension === "advertiser") {
      return mockAdvertisers.map((a) => {
        const spend = mockCampaigns.filter((c) => c.advertiser_id === a.id).reduce((s, c) => s + (c.spent_total || 0), 0)
        return rowFor(a.id, a.name, a.status, spend)
      })
    }
    return mockCampaigns.map((c) => rowFor(c.id, c.name, c.status, c.spent_total || 0))
  }

  // --- Сущности (in-memory CRUD, см. crud()) ---
  if (path === "/advertisers" && method === "GET") return mockAdvertisers
  const advCrud = crud(method, path, body, "/advertisers", mockAdvertisers as MockRow[], {
    prefix: "adv",
    defaults: () => ({
      display_id: 100 + mockAdvertisers.length + 1,
      status: "active",
      balance: 0,
      currency: "RUB",
    }),
  })
  if (advCrud !== undefined) return advCrud

  if (path === "/groups" && method === "GET") return mockGroups
  const grpCrud = crud(method, path, body, "/groups", mockGroups as MockRow[], { prefix: "grp" })
  if (grpCrud !== undefined) return grpCrud

  if (path === "/line-items" || path === "/lineitems") return mockLineItems
  const liByCamp = path.match(/^\/campaigns\/([^/]+)\/line-items$/)
  if (liByCamp) return mockLineItems.filter((l) => l.campaign_id === liByCamp[1])
  const liCrud = crud(method, path, body, "/line-items", mockLineItems as MockRow[], {
    prefix: "li",
    defaults: () => ({ status: "active" }),
  })
  if (liCrud !== undefined) return liCrud

  if (path === "/creatives" && method === "GET") {
    const cid = search.get("campaign_id")
    return cid ? mockCreatives.filter((c) => c.campaign_id === cid) : mockCreatives
  }
  const crCrud = crud(method, path, body, "/creatives", mockCreatives as MockRow[], {
    prefix: "cr",
    defaults: () => ({ status: "pending", erid: null, moderation_comment: null }),
  })
  if (crCrud !== undefined) return crCrud

  // --- ОРД / маркировка ---
  if (path === "/ord/counterparties" && method === "GET") return mockCounterparties
  const cpCrud = crud(method, path, body, "/ord/counterparties", mockCounterparties as MockRow[], {
    prefix: "cp",
    defaults: () => ({ ord_status: "draft", ord_error: null, ord_external_id: null, advertiser_id: null }),
  })
  if (cpCrud !== undefined) return cpCrud
  if (path === "/ord/contracts" && method === "GET") return mockContracts
  const ctCrud = crud(method, path, body, "/ord/contracts", mockContracts as MockRow[], {
    prefix: "ct",
    defaults: (b) => {
      // Бэк отдаёт имена сторон денормализованно — повторяем это в демо.
      const cb = b as { client_id?: string; contractor_id?: string }
      const byId = (id?: string) => mockCounterparties.find((c) => c.id === id)?.name ?? "—"
      return {
        ord_status: "draft",
        ord_error: null,
        ord_external_id: null,
        client_name: byId(cb.client_id),
        contractor_name: byId(cb.contractor_id),
      }
    },
  })
  if (ctCrud !== undefined) return ctCrud
  const ordRegMatch = path.match(/^\/ord\/(counterparties|contracts|creatives)\/([^/]+)\/register$/)
  if (ordRegMatch && method === "POST") {
    const list =
      ordRegMatch[1] === "counterparties" ? mockCounterparties : ordRegMatch[1] === "contracts" ? mockContracts : mockCreatives
    const row = (list as MockRow[]).find((r) => r.id === ordRegMatch[2])
    if (row) {
      if (ordRegMatch[1] === "creatives") {
        row.erid = `2VtzqwDemo${Date.now().toString(36).slice(-4)}`
      } else if (ordRegMatch[1] === "contracts") {
        // Как в бэке: договор регистрируется только между уже зарегистрированными сторонами.
        const ct = row as { client_id?: string; contractor_id?: string }
        const sides = [ct.client_id, ct.contractor_id].map((id) => mockCounterparties.find((c) => c.id === id))
        const missing = sides.find((s) => !s || s.ord_status !== "registered")
        if (missing !== undefined) {
          row.ord_status = "failed"
          row.ord_error = "Заказчик не зарегистрирован в ОРД — сначала отправьте контрагента"
        } else {
          row.ord_status = "registered"
          row.ord_error = null
          row.ord_external_id = `ct-ord-${Date.now().toString(36).slice(-4)}`
        }
      } else {
        row.ord_status = "registered"
        row.ord_error = null
        row.ord_external_id = `cp-ord-${Date.now().toString(36).slice(-4)}`
      }
    }
    return row ?? { ok: true }
  }
  if (path === "/ord/creatives") return mockCreatives.filter((c) => c.erid)
  if (path === "/ord/exports") return []
  if (path === "/settings/ord")
    return { configured: true, environment: "sandbox", api_key_masked: "demo····key", last_check: iso(36e5), last_check_ok: true }

  // --- Финансы ---
  if (path === "/billing/summary") return mockBillingSummary
  if (path.startsWith("/billing/transactions")) return mockTransactions

  // --- ML-слой (Часть B) ---
  if (path === "/stats/domain-quality")
    return {
      threshold: 0.5,
      domains: [
        { domain: "news-portal.ru", score: 0.91, imps: 48200, clicks: 610, wins: 50100, reasons: [], flagged: false, updated_at: iso(4 * 36e5) },
        { domain: "auto-catalog.ru", score: 0.84, imps: 21500, clicks: 240, wins: 22800, reasons: [], flagged: false, updated_at: iso(4 * 36e5) },
        { domain: "recipe-hub.ru", score: 0.6, imps: 9800, clicks: 30, wins: 3100, reasons: ["imp_no_win"], flagged: false, updated_at: iso(4 * 36e5) },
        { domain: "empty-widget.ru", score: 0.3, imps: 12100, clicks: 0, wins: 3500, reasons: ["zero_engage", "imp_no_win"], flagged: true, updated_at: iso(4 * 36e5) },
        { domain: "click-farm-blog.ru", score: 0.21, imps: 15400, clicks: 2900, wins: 15600, reasons: ["ctr_anomaly"], flagged: true, updated_at: iso(4 * 36e5) },
      ],
    }
  if (path === "/settings/ml/status")
    return {
      pctr_model: {
        version: 3,
        trained_at: iso(11 * 36e5),
        window_from: iso(14 * 24 * 36e5),
        window_to: iso(11 * 36e5),
        sample_events: 182_400,
        metrics: { auc_like: 0.71, segments: 412, global_ctr: 0.0124 },
        params: { window_days: 14, min_events: 200 },
      },
      domain_quality: { domains_scored: 128, flagged: 9, last_update: iso(4 * 36e5) },
      clean_traffic_campaigns: 2,
    }
  if (path === "/settings/throttle/subnets") return { prefixes: ["45.155.204", "193.169"], count: 2 }

  // --- Прочее ---
  if (path === "/users" && method === "GET") return mockUsers
  if (path === "/audit/entities") return [{ key: "campaigns", label: "Кампании" }, { key: "creatives", label: "Креативы" }, { key: "users", label: "Пользователи" }]
  if (path.startsWith("/audit/log")) {
    const now = Date.now()
    return {
      items: [
        { id: 3, user_id: "u1", user_email: "admin@example.com", user_role: "admin", method: "PATCH", path: "/campaigns/c-1", status_code: 200, entity: "campaigns", entity_id: "c-1", ip: "10.0.0.1", created_at: new Date(now - 60_000).toISOString() },
        { id: 2, user_id: "u2", user_email: "buyer@agency.ru", user_role: "manager", method: "POST", path: "/creatives", status_code: 201, entity: "creatives", entity_id: null, ip: "10.0.0.2", created_at: new Date(now - 3_600_000).toISOString() },
        { id: 1, user_id: null, user_email: null, user_role: null, method: "DELETE", path: "/users/u-9", status_code: 403, entity: "users", entity_id: "u-9", ip: "203.0.113.9", created_at: new Date(now - 86_400_000).toISOString() },
      ],
      next_before_id: null,
    }
  }
  const usrCrud = crud(method, path, body, "/users", mockUsers, {
    prefix: "u",
    defaults: () => ({ role: "manager", is_active: true }),
  })
  if (usrCrud !== undefined) return usrCrud
  // Компонент колокольчика ждёт форму { items, unread } — как отдаёт бэкенд.
  if (path === "/notifications")
    return {
      unread: 2,
      items: [
        {
          id: "n-1",
          kind: "moderation",
          title: "Креатив одобрен",
          body: "«Куртки 970×250» прошёл модерацию и зарегистрирован в ОРД.",
          link: "/creatives",
          read_at: null,
          created_at: iso(2 * 36e5),
        },
        {
          id: "n-2",
          kind: "budget",
          title: "Бюджет почти израсходован",
          body: "Кампания «Осень-2026 — куртки и пальто» израсходовала 85% дневного бюджета.",
          link: "/campaigns",
          read_at: null,
          created_at: iso(5 * 36e5),
        },
        {
          id: "n-3",
          kind: "system",
          title: "Отчёт готов",
          body: "Еженедельный отчёт по кампаниям сформирован и доступен в «Аналитике».",
          link: "/analytics",
          read_at: iso(20 * 36e5),
          created_at: iso(26 * 36e5),
        },
      ],
    }
  if (path === "/settings") return { timezone: "Europe/Moscow", currency: "RUB" }
  if (path === "/audiences" && method === "GET") return mockAudiences
  const audCrud = crud(method, path, body, "/audiences", mockAudiences, {
    prefix: "aud",
    defaults: () => ({ is_active: true, members: 0, pixel_name: "Сайт магазина курток" }),
  })
  if (audCrud !== undefined) return audCrud
  if (path === "/pixels" || path === "/pixel")
    return [
      {
        id: "px-demo-1",
        name: "Сайт магазина курток",
        domain: "jackets-store.ru",
        goals_count: 2,
        sessions_24h: 412,
        conversions_24h: 9,
        created_at: iso(20 * 24 * 36e5),
      },
    ]
  if (/^\/pixels\/[^/]+\/goals$/.test(path))
    return [
      { id: "g-1", name: "Покупка", goal_type: "event", match_value: "purchase", value: 1200, conversions_total: 87 },
      { id: "g-2", name: "Страница «Спасибо»", goal_type: "url", match_value: "/thank-you", value: 0, conversions_total: 102 },
    ]
  if (/^\/pixels\/[^/]+\/stats$/.test(path))
    return {
      sessions: 2840,
      pageviews: 7120,
      avg_session_sec: 74.5,
      ad_sessions: 1130,
      conversions: 96,
      avg_depth: 2.51,
      bounce_rate: 38.2,
      avg_scroll_pct: 54.7,
      by_source: [
        { source: "news-portal.ru", conversions: 41, value: 49200 },
        { source: "auto-catalog.ru", conversions: 22, value: 26400 },
        { source: "organic", conversions: 33, value: 0 },
      ],
    }
  if (path === "/rules/evaluate" && method === "POST") return { evaluated: 0, triggered: 0, details: [] }
  if (path === "/rules/templates")
    return [
      { key: "pause_low_ctr", name: "Пауза при низком CTR", description: "Кампания тратит, но не кликается — остановить и разобраться.", metric: "ctr", operator: "lt", threshold: 0.05, action: "pause_campaign", action_value: null, window_hours: 24 },
      { key: "pause_budget_burn", name: "Стоп при израсходовании 95% бюджета", description: "Страховка от перерасхода: пауза, когда бюджет почти исчерпан.", metric: "budget_spent_pct", operator: "gte", threshold: 95, action: "pause_campaign", action_value: null, window_hours: 24 },
      { key: "cpa_guard", name: "Снизить ставку при дорогом CPA", description: "Конверсия дороже 1500 ₽ — снижаем ставку на 15%, охлаждаем закупку.", metric: "cpa", operator: "gt", threshold: 1500, action: "decrease_bid", action_value: 15, window_hours: 48 },
      { key: "cpc_guard", name: "Снизить ставку при дорогом клике", description: "CPC выше 50 ₽ за сутки — снижаем ставку на 10%.", metric: "cpc", operator: "gt", threshold: 50, action: "decrease_bid", action_value: 10, window_hours: 24 },
      { key: "scale_high_cvr", name: "Масштабировать при высоком CVR", description: "Конверсия из клика выше 5% — повышаем дневной бюджет на 20%.", metric: "cvr", operator: "gt", threshold: 5, action: "increase_daily_budget", action_value: 20, window_hours: 72 },
      { key: "auto_blacklist", name: "Автоблэклист площадок без кликов", description: "Домены с 500+ показов и нулём кликов за 3 дня — в чёрный список.", metric: "impressions", operator: "gt", threshold: 500, action: "blacklist_domains", action_value: 500, window_hours: 72 },
      { key: "low_win_rate", name: "Поднять ставку при низком Win Rate", description: "Выигрываем меньше 5% аукционов — ставка неконкурентна, поднимаем на 10%.", metric: "win_rate", operator: "lt", threshold: 5, action: "increase_bid", action_value: 10, window_hours: 24 },
      { key: "notify_spend_spike", name: "Уведомление о всплеске расхода", description: "Расход за сутки превысил 50 000 ₽ — зафиксировать срабатывание.", metric: "spend", operator: "gt", threshold: 50000, action: "notify", action_value: null, window_hours: 24 },
    ]
  if (path === "/rules" && method === "GET") return mockRules
  const ruleCrud = crud(method, path, body, "/rules", mockRules, {
    prefix: "rule",
    defaults: () => ({ status: "active", last_triggered_at: null, message: null }),
  })
  if (ruleCrud !== undefined) return ruleCrud
  if (path === "/share/links" || path === "/share/guests") return []

  // --- Онбординг SSP (Часть SSP) ---
  if (path === "/supply-partners" && method === "GET") return mockSupplyPartners
  if (path.match(/^\/supply-partners\/[^/]+\/check$/) && method === "POST") return mockSspCheck
  if (path.match(/^\/supply-partners\/[^/]+\/stats$/) && method === "GET") return mockSspStats()
  if (path.match(/^\/supply-partners\/[^/]+\/token$/) && method === "POST")
    return { token: "csp_demo_token_4f8a1b2c3d4e5f60718293a4b5c6d7e8" }
  const sspCrud = crud(method, path, body, "/supply-partners", mockSupplyPartners as MockRow[], {
    prefix: "ssp",
    defaults: (b) => ({ name: (b as { name?: string }).name || "Новый SSP", status: "draft", last_check: null }),
  })
  if (sspCrud !== undefined) return sspCrud

  // --- Скоринг качества площадок (Часть 3b) ---
  // Демо-состояние живёт в памяти процесса: POST/PATCH/DELETE реально меняют список,
  // чтобы кабинет вёл себя как с настоящим бэком.
  if (path === "/scoring/profiles" && method === "GET") return mockScoringProfiles
  if (path === "/scoring/profiles" && method === "POST") {
    const b = (body && typeof body === "object" ? body : {}) as Partial<ScoringProfile>
    const created: ScoringProfile = {
      ...mockScoringProfiles[0],
      ...b,
      id: `sp-${Date.now()}`,
      name: b.name || "Новый профиль",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }
    mockScoringProfiles.unshift(created)
    return created
  }
  const spMatch = path.match(/^\/scoring\/profiles\/([^/]+)$/)
  if (spMatch && method === "PATCH") {
    const idx = mockScoringProfiles.findIndex((p) => p.id === spMatch[1])
    if (idx < 0) throw new Error("Профиль не найден")
    const b = (body && typeof body === "object" ? body : {}) as Partial<ScoringProfile>
    mockScoringProfiles[idx] = { ...mockScoringProfiles[idx], ...b, id: spMatch[1], updated_at: new Date().toISOString() }
    return mockScoringProfiles[idx]
  }
  if (spMatch && method === "DELETE") {
    const idx = mockScoringProfiles.findIndex((p) => p.id === spMatch[1])
    if (idx >= 0) mockScoringProfiles.splice(idx, 1)
    return { ok: true }
  }
  if (path === "/scoring/report" || path === "/scoring/report.csv") {
    const dim = search.get("dimension") || "domain"
    const profile = mockScoringProfiles.find((p) => p.id === search.get("profile_id")) || mockScoringProfiles[0]
    const sort = search.get("sort") || "score"
    const order = search.get("order") || "asc"
    const q = (search.get("q") || "").toLowerCase()
    const truthy = (v: string | null) => v === "1" || v === "true"
    const onlyBlocked = truthy(search.get("blocked")) || truthy(search.get("only_blocked"))
    const onlyFlagged = truthy(search.get("flagged")) || truthy(search.get("only_flagged"))
    const page = Number(search.get("page") || 1)
    const pageSize = Number(search.get("page_size") || 25)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let rows: any[] = (dim === "ssp" ? mockScoringSsp : mockScoringDomains).map((r) => ({
      blocked: false,
      block_source: null,
      ...r,
    }))
    rows = rows.map((r) =>
      mockManualBlocks.has(r.domain) ? { ...r, blocked: true, block_source: "manual" } : r,
    )
    if (q) rows = rows.filter((r) => (r.domain || r.name || "").toLowerCase().includes(q))
    if (onlyBlocked) rows = rows.filter((r) => r.blocked)
    if (onlyFlagged) rows = rows.filter((r) => r.flagged)
    rows.sort((a, b) => {
      if (sort === "domain" || sort === "name") {
        const c = (a.domain || a.name || "").localeCompare(b.domain || b.name || "", "ru")
        return order === "desc" ? -c : c
      }
      const av = a[sort], bv = b[sort]
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      return order === "desc" ? bv - av : av - bv
    })
    const total = rows.length
    if (path === "/scoring/report.csv") {
      const cols = ["domain", "score", "impressions", "clicks", "ctr", "viewability", "cpa", "spend", "blocked", "reasons"]
      const esc = (v: unknown) => {
        const s = v == null ? "" : Array.isArray(v) ? v.join("|") : String(v)
        return /[";\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
      }
      const lines = [cols.join(";"), ...rows.map((r) => cols.map((c) => esc(c === "domain" ? r.domain || r.name : r[c])).join(";"))]
      return { __csv: "\uFEFF" + lines.join("\n"), __filename: `scoring-${dim}.csv` }
    }
    return {
      profile,
      dimension: dim,
      rows: rows.slice((page - 1) * pageSize, page * pageSize),
      total,
      total_flagged: rows.filter((r) => r.flagged).length,
      total_blocked: rows.filter((r) => r.blocked).length,
      page,
      page_size: pageSize,
      sort,
      order,
      window_days: profile.window_days ?? 7,
      generated_at: new Date().toISOString(),
    }
  }
  if (path === "/scoring/blocks" && method === "POST") {
    const d = String((body as { domain?: string })?.domain || "").toLowerCase()
    mockManualBlocks.add(d)
    return { ok: true, domain: d, source: "manual" }
  }
  if (path.startsWith("/scoring/blocks/") && method === "DELETE") {
    mockManualBlocks.delete(decodeURIComponent(path.split("/").pop() || ""))
    return { ok: true, removed: true }
  }
  if (path === "/scoring/recompute" && method === "POST") return { ok: true, scored: 42, blocked: 6 }

  return undefined
}

// Демо-данные онбординга SSP
function mockSspStats() {
  const m = (bids: number, wins: number, imps: number, clicks: number, losses: number, spend: number) => ({
    bids, wins, impressions: imps, clicks, losses, spend,
    win_rate: bids ? Math.round((wins / bids) * 10000) / 100 : null,
    ctr: imps ? Math.round((clicks / imps) * 10000) / 100 : null,
  })
  const day = 24 * 3600 * 1000
  return {
    days: 7,
    totals: { ...m(48200, 6120, 5890, 71, 39800, 9420.5), avg_bid: 112.4, avg_bidfloor: 85.0, avg_min_to_win: 131.7 },
    daily: Array.from({ length: 7 }, (_, i) => ({
      ts: new Date(Date.now() - (6 - i) * day).toISOString().slice(0, 10) + "T00:00:00+00:00",
      ...m(6500 + i * 200, 800 + i * 40, 780 + i * 38, 9 + (i % 3), 5400 + i * 150, 1250 + i * 60),
    })),
    loss_reasons: [
      { reason: "102", label: "Проиграли по цене", count: 31200, share: 78.39 },
      { reason: "100", label: "Ставка ниже bidfloor", count: 6100, share: 15.33 },
      { reason: "2", label: "Показ не состоялся (timeout)", count: 2500, share: 6.28 },
    ],
    placements: [
      { publisher_id: "pub-avito", tagid: "avito_main_300x250", site: "avito.ru", ...m(21000, 3100, 2980, 40, 17200, 4800) },
      { publisher_id: "pub-avito", tagid: "avito_item_728x90", site: "avito.ru", ...m(15400, 1900, 1830, 19, 13100, 2900) },
      { publisher_id: "pub-news", tagid: "news_side_240x400", site: "news.example", ...m(11800, 1120, 1080, 12, 9500, 1720.5) },
    ],
    ad_types: [
      { ad_type: "banner", ...m(44000, 5800, 5590, 66, 36500, 8900) },
      { ad_type: "video", ...m(4200, 320, 300, 5, 3300, 520.5) },
    ],
  }
}

const mockSspCheck = {
  ran_at: iso(0),
  passed: true,
  checks: [
    { key: "endpoint", label: "Bid-эндпоинт отвечает", ok: true, detail: "POST /rtb/bid/{key} — 200/204" },
    { key: "auth", label: "Аутентификация настроена", ok: true, detail: "Секретный токен задан, заголовок проверяется" },
    { key: "parse", label: "BidRequest парсится", ok: true, detail: "OpenRTB 2.5, banner 300x250 — запрос принят" },
    { key: "response", label: "BidResponse валиден", ok: true, detail: "seatbid, price, adm, nurl присутствуют" },
    { key: "macros", label: "Макросы аукциона", ok: true, detail: "${AUCTION_PRICE} в nurl, win-notice принимается" },
    { key: "tmax", label: "Ответ в пределах tmax", ok: true, detail: "38 мс < 200 мс" },
    { key: "nobid", label: "Корректный no-bid", ok: true, detail: "204 No Content при пустом таргетинге" },
  ],
}

const mockSupplyPartners = [
  {
    id: "ssp-demo-1",
    display_id: 1,
    name: "Yandex ADFOX",
    endpoint_key: "adfx_9f2e1a7b",
    status: "active",
    protocol: "openrtb",
    protocol_version: "2.5",
    auction_type: 2,
    bid_floor: 5,
    qps_limit: 2000,
    allowed_ad_types: ["banner", "native", "video"],
    currency: "RUB",
    tmax_ms: 200,
    ip_whitelist: ["77.88.0.0/18", "5.255.192.0/18"],
    seat_id: "seat-cdsp-041",
    contact_email: "rtb@adfox.example",
    notes: "Основной источник. Прод с 12.08.",
    has_auth_token: true,
    auth_token_hint: "csp_...d7e8",
    last_check: mockSspCheck,
    created_at: iso(240 * 36e5),
  },
  {
    id: "ssp-demo-2",
    display_id: 2,
    name: "Between Exchange",
    endpoint_key: "btwn_3c8d4e5f",
    status: "testing",
    protocol: "openrtb",
    protocol_version: "2.5",
    auction_type: 1,
    bid_floor: 12,
    qps_limit: 500,
    allowed_ad_types: ["banner", "video"],
    currency: "RUB",
    tmax_ms: 300,
    ip_whitelist: [],
    seat_id: null,
    contact_email: "partners@betweenx.example",
    notes: "Тестовый трафик, ждём seat ID.",
    has_auth_token: true,
    auth_token_hint: "csp_...91af",
    last_check: {
      ran_at: iso(5 * 36e5),
      passed: false,
      checks: [
        { key: "endpoint", label: "Bid-эндпоинт отвечает", ok: true, detail: "POST /rtb/bid/{key} — 200/204" },
        { key: "auth", label: "Аутентификация настроена", ok: true, detail: "Секретный токен задан" },
        { key: "parse", label: "BidRequest парсится", ok: true, detail: "OpenRTB 2.5, banner — запрос принят" },
        { key: "response", label: "BidResponse валиден", ok: false, detail: "Нет активных кампаний под тип video — нечем ответить" },
        { key: "macros", label: "Макросы аукциона", ok: true, detail: "${AUCTION_PRICE} в nurl" },
        { key: "tmax", label: "Ответ в пределах tmax", ok: true, detail: "41 мс < 300 мс" },
        { key: "nobid", label: "Корректный no-bid", ok: true, detail: "204 No Content" },
      ],
    },
    created_at: iso(48 * 36e5),
  },
  {
    id: "ssp-demo-3",
    display_id: 3,
    name: "MoevideoX (черновик)",
    endpoint_key: "mvx_1a2b3c4d",
    status: "draft",
    protocol: "openrtb",
    protocol_version: "2.6",
    auction_type: 2,
    bid_floor: 0,
    qps_limit: 100,
    allowed_ad_types: ["video"],
    currency: "USD",
    tmax_ms: 250,
    ip_whitelist: [],
    seat_id: null,
    contact_email: null,
    notes: null,
    has_auth_token: false,
    auth_token_hint: null,
    last_check: null,
    created_at: iso(2 * 36e5),
  },
]

// Демо-данные скоринга (mutable: POST/PATCH/DELETE в resolveMock меняют список)
export const mockScoringProfiles: ScoringProfile[] = [
  {
    id: "sp-brand",
    name: "Бренд-безопасность (жёсткий)",
    description: "Только премиальный инвентарь: высокий viewability, живое поведение.",
    window_days: 14,
    min_impressions: 300,
    min_clicks: 0,
    min_ctr: 0.08,
    min_viewability: 60,
    max_bounce_rate: 65,
    min_avg_depth: 1.8,
    min_time_on_site: 25,
    max_cpa: null,
    goal_id: null,
    pixel_id: "px-demo-1",
    block_threshold: 0.5,
    enforce: true,
    created_at: iso(72 * 36e5),
    updated_at: iso(2 * 36e5),
  },
  {
    id: "sp-perf",
    name: "Перформанс по CPA",
    description: "Отсекаем площадки с дорогой конверсией по цели «Покупка».",
    window_days: 7,
    min_impressions: 200,
    min_clicks: 5,
    min_ctr: 0.05,
    min_viewability: null,
    max_bounce_rate: null,
    min_avg_depth: null,
    min_time_on_site: null,
    max_cpa: 1500,
    goal_id: "goal-demo-1",
    pixel_id: "px-demo-1",
    block_threshold: 0.5,
    enforce: false,
    created_at: iso(120 * 36e5),
    updated_at: iso(30 * 36e5),
  },
]

const mockScoringDomains = [
  { domain: "clickfarm-xyz.ru", impressions: 18400, clicks: 2210, viewable_imps: 6100, spend: 2576, conversions: 0, ctr: 12.01, viewability: 33.2, avg_depth: 1.02, avg_time: 4.1, avg_scroll: 8, bounce_rate: 92, cpa: null, score: 0.0, reasons: ["low_ctr", "low_viewability", "high_bounce"], flagged: true },
  { domain: "cheap-traffic.net", impressions: 9200, clicks: 41, viewable_imps: 3100, spend: 1288, conversions: 1, ctr: 0.45, viewability: 33.7, avg_depth: 1.1, avg_time: 6, bounce_rate: 88, cpa: 1288, score: 0.1, reasons: ["low_viewability", "high_bounce", "high_cpa"], flagged: true },
  { domain: "news-portal.ru", impressions: 42100, clicks: 512, viewable_imps: 30200, spend: 5894, conversions: 8, ctr: 1.22, viewability: 71.7, avg_depth: 3.4, avg_time: 62, bounce_rate: 41, cpa: 736, score: 1.0, reasons: [], flagged: false },
  { domain: "sport-live.com", impressions: 31500, clicks: 388, viewable_imps: 21400, spend: 4410, conversions: 6, ctr: 1.23, viewability: 67.9, avg_depth: 2.9, avg_time: 54, bounce_rate: 48, cpa: 735, score: 1.0, reasons: [], flagged: false },
  { domain: "weather-widget.io", impressions: 5400, clicks: 12, viewable_imps: 1900, spend: 756, conversions: 0, ctr: 0.22, viewability: 35.2, avg_depth: 1.3, avg_time: 9, bounce_rate: 79, cpa: null, score: 0.5, reasons: ["low_viewability"], flagged: false },
]

const mockScoringSsp = [
  { id: "ssp-1", name: "Yandex ADFOX", impressions: 128000, clicks: 1840, viewable_imps: 92000, spend: 17920, ctr: 1.44, viewability: 71.9, avg_depth: null, avg_time: null, bounce_rate: null, cpa: null, score: 1.0, reasons: [], flagged: false },
  { id: "ssp-2", name: "Between Exchange", impressions: 64000, clicks: 410, viewable_imps: 28000, spend: 8960, ctr: 0.64, viewability: 43.8, avg_depth: null, avg_time: null, bounce_rate: null, cpa: null, score: 1.0, reasons: [], flagged: false },
  { id: "ssp-3", name: "GetIntent (тест)", impressions: 21000, clicks: 2510, viewable_imps: 7100, spend: 2940, ctr: 11.95, viewability: 33.8, avg_depth: null, avg_time: null, bounce_rate: null, cpa: null, score: 0.0, reasons: ["low_ctr", "low_viewability"], flagged: true },
]
