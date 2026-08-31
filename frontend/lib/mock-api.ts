/**
 * Demo-данные для превью v0 (песочница без Python-бэкенда и Postgres).
 *
 * Используются ТОЛЬКО как fallback в app/api/[...path]/route.ts, когда реальный
 * FastAPI недоступен. В проде vercel.json направляет /api на Python-бэкенд до
 * Next.js, поэтому этот модуль в прод-трафике не участвует.
 */

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
    targeting: { geo_countries: ["RU"], device_types: ["desktop"], ad_types: ["banner"] },
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
    targeting: { geo_countries: ["RU"], device_types: ["mobile"], ad_types: ["native"] },
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
    targeting: { geo_countries: ["RU"], ad_types: ["video"] },
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

export const mockCounterparties = [
  {
    id: "cp-1",
    name: "ООО «Северный Свет»",
    inn: "7810123456",
    ogrn: "1027810123456",
    legal_address: "г. Санкт-Петербург, Невский пр., д. 1",
    type: "ul",
    status: "registered",
    ord_external_id: "cp-ord-1",
    created_at: iso(60 * 864e5),
  },
]

export const mockContracts = [
  {
    id: "ct-1",
    counterparty_id: "cp-1",
    number: "ДС-2026/14",
    date: "2026-01-15",
    amount: 450_000,
    subject: "Распространение рекламы",
    status: "registered",
    ord_external_id: "ct-ord-1",
    created_at: iso(50 * 864e5),
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

export function mockTimeseries(hours: number) {
  const pts = []
  for (let i = 1; i <= hours; i++) pts.push(seriesPoint(i, hours))
  return pts
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
export function resolveMock(method: string, path: string, search: URLSearchParams): unknown {
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
    return { ...src, id: `${src.id}-copy`, display_id: 99, name: `${src.name} (копия)`, status: "draft", spent_today: 0, spent_total: 0 }
  }
  const campMatch = path.match(/^\/campaigns\/([^/]+)$/)
  if (campMatch && method === "GET") {
    return mockCampaigns.find((c) => c.id === campMatch[1]) ?? mockCampaigns[0]
  }

  // --- Line items: привязка креативов (G9) ---
  const liCrMatch = path.match(/^\/line-items\/([^/]+)\/creatives$/)
  if (liCrMatch) return { line_item_id: liCrMatch[1], creative_ids: [] }

  // --- Статистика ---
  if (path === "/stats/summary")
    return mockSummary(Number(search.get("hours") || 24), search.get("campaign_id"))
  if (path === "/stats/timeseries") return mockTimeseries(Math.min(Number(search.get("hours") || 24), 168))
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
        return acc
      },
      { impressions: 0, clicks: 0, spend: 0, conversions: 0, bids: 0, wins: 0 } as Record<string, number>,
    )
    totals.ctr = totals.impressions ? Math.round((totals.clicks / totals.impressions) * 100 * 1000) / 1000 : 0
    totals.win_rate = totals.bids ? Math.round((totals.wins / totals.bids) * 100 * 100) / 100 : 0
    totals.cpm = totals.impressions ? (totals.spend / totals.impressions) * 1000 : 0
    totals.cpc = totals.clicks ? totals.spend / totals.clicks : 0
    totals.cpa = totals.conversions ? totals.spend / totals.conversions : 0
    return { rows, totals }
  }
  if (path.startsWith("/stats/breakdown")) return []

  // --- Сущности ---
  if (path === "/advertisers") return mockAdvertisers
  if (path === "/groups" && method === "GET") return mockGroups
  if (path === "/line-items" || path === "/lineitems") return mockLineItems
  const liByCamp = path.match(/^\/campaigns\/([^/]+)\/line-items$/)
  if (liByCamp) return mockLineItems.filter((l) => l.campaign_id === liByCamp[1])
  if (path === "/creatives") {
    const cid = search.get("campaign_id")
    return cid ? mockCreatives.filter((c) => c.campaign_id === cid) : mockCreatives
  }

  // --- ОРД / маркировка ---
  if (path === "/ord/counterparties") return mockCounterparties
  if (path === "/ord/contracts") return mockContracts
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
  if (path === "/users") return [mockUser]
  if (path === "/notifications") return []
  if (path === "/settings") return { timezone: "Europe/Moscow", currency: "RUB" }
  if (path === "/audiences") return []
  if (path === "/pixels" || path === "/pixel") return []
  if (path === "/rules") return []
  if (path === "/supply-partners") return []
  if (path === "/share/links" || path === "/share/guests") return []

  return undefined
}
